package io.finreceipts;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

@Component
class Scheduler {
  final PlatformService service;
  final TransactionTemplate tx;
  final RabbitTemplate rabbit;
  final boolean enabled;
  final MeterRegistry metrics;
  @org.springframework.beans.factory.annotation.Autowired RateControl rate;

  Scheduler(
      PlatformService service,
      TransactionTemplate tx,
      RabbitTemplate rabbit,
      MeterRegistry metrics,
      @Value("${finreceipts.scheduler-enabled}") boolean enabled) {
    this.service = service;
    this.tx = tx;
    this.rabbit = rabbit;
    this.enabled = enabled;
    this.metrics = metrics;
  }

  @Bean
  Queue workQueue() {
    return new Queue("finreceipts.v2.steps", true, false, false, Map.of("x-queue-type", "quorum"));
  }

  @Scheduled(fixedDelay = 200)
  public void dispatch() {
    if (!enabled) return;
    try {
      rate.health();
    } catch (Exception e) {
      metrics.counter("finreceipts_dispatch_failures").increment();
      return;
    }
    for (int i = 0; i < 16; i++) {
      try {
        if (!Boolean.TRUE.equals(tx.execute(s -> dispatchOne()))) break;
      } catch (Exception e) {
        metrics.counter("finreceipts_dispatch_failures").increment();
        break;
      }
    }
  }

  boolean dispatchOne() {
    var db = service.jdbc();
    var tenants =
        db.queryForList(
            "SELECT * FROM tenants t WHERE EXISTS (SELECT 1 FROM tasks j WHERE j.tenant_id=t.id AND j.status='QUEUED') ORDER BY last_dispatch,id LIMIT 1 FOR UPDATE SKIP LOCKED");
    if (tenants.isEmpty()) return false;
    var tenant = tenants.getFirst();
    String tenantId = tenant.get("id").toString();
    db.update("UPDATE tenants SET last_dispatch=? WHERE id=?", Json.now(), tenantId);
    long running =
        db.queryForObject(
            "SELECT COUNT(*) FROM tasks WHERE tenant_id=? AND status IN ('READY','RUNNING')",
            Long.class,
            tenantId);
    if (running >= Json.number(tenant, "max_running")) return false;
    var rows =
        db.queryForList(
            "SELECT o.id outbox_id,j.id,j.revision,j.traceparent FROM tasks j JOIN outbox o ON o.task_id=j.id AND o.revision=j.revision WHERE j.tenant_id=? AND j.status='QUEUED' AND o.published=false ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE SKIP LOCKED",
            tenantId);
    if (rows.isEmpty()) return false;
    var row = rows.getFirst();
    long revision = Json.number(row, "revision") + 1;
    db.update(
        "UPDATE tasks SET status='READY',revision=?,lease_until=? WHERE id=?",
        revision,
        Json.now() + service.leaseMillis(),
        row.get("id"));
    db.update("UPDATE outbox SET revision=? WHERE id=?", revision, row.get("outbox_id"));
    service.event(row.get("id").toString(), tenantId, revision, "task_scheduled", Map.of());
    return true;
  }

  @Scheduled(fixedDelay = 200)
  public void publish() {
    if (!enabled) return;
    try {
      rate.health();
      for (int i = 0; i < 16; i++) if (!publishOne()) break;
    } catch (Exception e) {
      metrics.counter("finreceipts_dispatch_failures").increment();
    }
  }

  boolean publishOne() {
    var db = service.jdbc();
    String token = Json.id();
    var row =
        tx.execute(
            s -> {
              var rows =
                  db.queryForList(
                      "SELECT o.id outbox_id,j.id,j.revision,j.traceparent FROM outbox o JOIN tasks j ON j.id=o.task_id AND j.revision=o.revision WHERE o.published=false AND o.publish_until<? AND j.status='READY' ORDER BY o.created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
                      Json.now());
              if (rows.isEmpty()) return null;
              var selected = rows.getFirst();
              db.update(
                  "UPDATE outbox SET publish_token=?,publish_until=? WHERE id=?",
                  token,
                  Json.now() + 10000,
                  selected.get("outbox_id"));
              return selected;
            });
    if (row == null) return false;
    var correlation = new CorrelationData(row.get("outbox_id").toString());
    var delivery = new LinkedHashMap<String, Object>();
    delivery.put("protocol_version", 1);
    delivery.put("task_id", row.get("id"));
    delivery.put("revision", row.get("revision"));
    delivery.put("traceparent", row.get("traceparent"));
    try {
      // No SQL transaction or task/tenant lock is held across broker I/O.
      rabbit.convertAndSend("", "finreceipts.v2.steps", Json.write(delivery), correlation);
      var confirm = correlation.getFuture().get(3, TimeUnit.SECONDS);
      if (!confirm.isAck() || correlation.getReturned() != null)
        throw new IllegalStateException("broker_rejected");
    } catch (Exception e) {
      throw new IllegalStateException("broker_unconfirmed", e);
    }
    db.update(
        "UPDATE outbox SET published=true,publish_token=NULL WHERE id=? AND publish_token=?",
        row.get("outbox_id"),
        token);
    metrics.counter("finreceipts_step_dispatches").increment();
    return true;
  }

  @Scheduled(fixedDelay = 5000)
  public void recover() {
    if (!enabled) return;
    try {
      tx.executeWithoutResult(
          s -> {
            var db = service.jdbc();
            var rows =
                db.queryForList(
                    "SELECT * FROM tasks WHERE status IN ('READY','RUNNING') AND lease_until<? ORDER BY lease_until LIMIT 32 FOR UPDATE SKIP LOCKED",
                    Json.now());
            for (var t : rows) {
              String id = t.get("id").toString();
              long dispatched =
                  db.queryForObject(
                      "SELECT COUNT(*) FROM attempts WHERE task_id=? AND lease_token=?",
                      Long.class,
                      id,
                      t.get("lease_token"));
              boolean unknown = "RUNNING".equals(t.get("status")) && dispatched > 0;
              db.update(
                  "UPDATE attempts SET status='UNKNOWN' WHERE task_id=? AND status='RESERVED'", id);
              long revision = Json.number(t, "revision") + 1;
              db.update(
                  "UPDATE tasks SET status=?,revision=?,lease_token=NULL,lease_until=NULL,error_code=?,updated_at=? WHERE id=?",
                  unknown ? "UNKNOWN" : "QUEUED",
                  revision,
                  unknown ? "lease_expired_after_dispatch" : null,
                  Json.now(),
                  id);
              service.event(
                  id,
                  t.get("tenant_id").toString(),
                  revision,
                  "lease_expired",
                  Map.of("unknown", unknown));
              if (!unknown) service.outbox(id, revision);
              metrics
                  .counter("finreceipts_lease_expirations", "unknown", String.valueOf(unknown))
                  .increment();
            }
          });
    } catch (Exception e) {
      metrics.counter("finreceipts_recovery_failures").increment();
    }
  }
}
