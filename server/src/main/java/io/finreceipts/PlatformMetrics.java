package io.finreceipts;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Cache gauges away from request/scrape threads; a broken DB makes freshness visible. */
@Component
class PlatformMetrics {
  final PlatformService service;
  final Map<String, AtomicLong> gauges = new HashMap<>();

  PlatformMetrics(PlatformService service, MeterRegistry registry) {
    this.service = service;
    for (String name :
        List.of(
            "queued_tasks",
            "running_tasks",
            "unknown_tasks",
            "reserved_micros",
            "spent_micros",
            "oldest_queue_seconds",
            "snapshot_epoch_seconds")) {
      var value = new AtomicLong();
      gauges.put(name, value);
      registry.gauge("finreceipts_" + name, value);
    }
  }

  @Scheduled(fixedDelay = 5000)
  void refresh() {
    try {
      var counts =
          service
              .jdbc()
              .queryForMap(
                  "SELECT SUM(status='QUEUED') queued_tasks,SUM(status IN ('READY','RUNNING')) running_tasks,SUM(status='UNKNOWN') unknown_tasks,MIN(CASE WHEN status='QUEUED' THEN updated_at END) oldest FROM tasks");
      for (String key : List.of("queued_tasks", "running_tasks", "unknown_tasks"))
        gauges.get(key).set(counts.get(key) == null ? 0 : Json.number(counts, key));
      var budget =
          service
              .jdbc()
              .queryForMap(
                  "SELECT reserved_micros,spent_micros FROM provider_budget WHERE id='global'");
      for (String key : List.of("reserved_micros", "spent_micros"))
        gauges.get(key).set(Json.number(budget, key));
      gauges
          .get("oldest_queue_seconds")
          .set(
              counts.get("oldest") == null
                  ? 0
                  : (Json.now() - Json.number(counts, "oldest")) / 1000);
      gauges.get("snapshot_epoch_seconds").set(Json.now() / 1000);
    } catch (Exception ignored) {
      /* The freshness gauge remains old instead of reporting false zeroes. */
    }
  }
}
