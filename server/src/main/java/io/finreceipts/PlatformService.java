package io.finreceipts;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PlatformService {
  final JdbcTemplate db;
  final int modelConcurrency;
  final long leaseMillis;
  final String provider, model;
  @org.springframework.beans.factory.annotation.Autowired io.micrometer.tracing.Tracer tracer;

  @org.springframework.beans.factory.annotation.Autowired
  io.micrometer.core.instrument.MeterRegistry metrics;

  PlatformService(
      JdbcTemplate db,
      @Value("${finreceipts.model-concurrency}") int concurrency,
      @Value("${finreceipts.lease-seconds}") long seconds,
      @Value("${finreceipts.model-provider}") String provider,
      @Value("${finreceipts.model-id}") String model) {
    this.db = db;
    modelConcurrency = concurrency;
    leaseMillis = seconds * 1000;
    this.provider = provider;
    this.model = model;
  }

  public JdbcTemplate jdbc() {
    return db;
  }

  void artifact(String kind, Object document) {
    byte[] bytes = Json.write(document).getBytes(StandardCharsets.UTF_8);
    db.update(
        "INSERT IGNORE INTO artifact_copies(digest,kind,document,created_at) VALUES (?,?,?,?)",
        Json.hashBytes(bytes),
        kind,
        bytes,
        Json.now());
  }

  public long leaseMillis() {
    return leaseMillis;
  }

  Map<String, Object> member(String username) {
    var rows =
        db.queryForList(
            "SELECT id,tenant_id,username,role FROM members WHERE username=? AND enabled=true",
            username);
    if (rows.isEmpty()) throw new Problem(401, "authentication_required");
    return rows.getFirst();
  }

  static void role(Map<String, Object> m, String... roles) {
    if (!Arrays.asList(roles).contains(m.get("role"))) throw new Problem(403, "role_denied");
  }

  Map<String, Object> task(String id, String tenant, boolean lock) {
    var rows =
        db.queryForList(
            "SELECT * FROM tasks WHERE id=? AND tenant_id=?" + (lock ? " FOR UPDATE" : ""),
            id,
            tenant);
    if (rows.isEmpty()) throw new Problem(404, "task_not_found");
    return rows.getFirst();
  }

  Map<String, Object> workerTask(String id, String token) {
    var rows = db.queryForList("SELECT * FROM tasks WHERE id=? FOR UPDATE", id);
    if (rows.isEmpty()) throw new Problem(404, "task_not_found");
    var t = rows.getFirst();
    if (!"RUNNING".equals(t.get("status"))
        || !token.equals(t.get("lease_token"))
        || Json.number(t, "lease_until") <= Json.now()) throw Problem.conflict("stale_lease");
    return t;
  }

  void event(String id, String tenant, long revision, String kind, Object data) {
    var document = new LinkedHashMap<String, Object>();
    document.put("payload", data);
    if (tracer.currentSpan() != null)
      document.put("trace_id", tracer.currentSpan().context().traceId());
    db.update(
        "INSERT INTO events(task_id,tenant_id,seq,kind,document,created_at) VALUES (?,?,?,?,?,?)",
        id,
        tenant,
        revision,
        kind,
        Json.write(document),
        Json.now());
  }

  void outbox(String id, long revision) {
    db.update(
        "INSERT INTO outbox(id,task_id,revision,created_at) VALUES (?,?,?,?)",
        Json.id(),
        id,
        revision,
        Json.now());
  }

  @Transactional
  public Map<String, Object> create(Map<String, Object> m, String key, Contracts.Batch req) {
    role(m, "ADMIN", "ANALYST");
    if (key == null || !key.matches("[A-Za-z0-9_.:-]{1,100}"))
      throw new IllegalArgumentException("invalid_key");
    String tenant = m.get("tenant_id").toString(), user = m.get("id").toString();
    var quota = db.queryForMap("SELECT * FROM tenants WHERE id=? FOR UPDATE", tenant);
    String digest = Json.hash(req);
    var prior =
        db.queryForList(
            "SELECT id,request_digest FROM batches WHERE tenant_id=? AND creator_id=? AND request_key=?",
            tenant,
            user,
            key);
    if (!prior.isEmpty()) {
      if (!digest.equals(prior.getFirst().get("request_digest")))
        throw Problem.conflict("idempotency_conflict");
      return Map.of("batch_id", prior.getFirst().get("id"), "reused", true);
    }
    long pending =
        db.queryForObject(
            "SELECT COUNT(*) FROM tasks WHERE tenant_id=? AND status IN ('QUEUED','READY','RUNNING')",
            Long.class,
            tenant);
    if (pending + req.items().size() > Json.number(quota, "max_pending"))
      throw new Problem(429, "tenant_queue_full");
    var profile =
        Map.of(
            "protocol_version",
            1,
            "provider",
            provider,
            "model",
            model,
            "effort",
            "medium",
            "max_output_tokens",
            4096,
            "max_attempts",
            6,
            "max_revisions",
            2,
            "policy_version",
            "target-v2.0");
    String profileId = Json.hash(profile), batch = Json.id();
    long now = Json.now();
    db.update(
        "INSERT IGNORE INTO execution_profiles(id,document,created_at) VALUES (?,?,?)",
        profileId,
        Json.write(profile),
        now);
    db.update(
        "INSERT INTO batches(id,tenant_id,creator_id,request_key,request_digest,title,item_count,created_at) VALUES (?,?,?,?,?,?,?,?)",
        batch,
        tenant,
        user,
        key,
        digest,
        req.title(),
        req.items().size(),
        now);
    for (var item : req.items()) {
      if (item.kind() == null || item.ticker() == null || item.target().kind() == null)
        throw new IllegalArgumentException("missing_task_fields");
      String id = Json.id();
      db.update(
          "INSERT INTO tasks(id,batch_id,tenant_id,creator_id,profile_id,request,checkpoint,created_at,updated_at) VALUES (?,?,?,?,?,?,?, ?,?)",
          id,
          batch,
          tenant,
          user,
          profileId,
          Json.write(item),
          "{}",
          now,
          now);
      if (tracer.currentSpan() != null) {
        var ctx = tracer.currentSpan().context();
        db.update(
            "UPDATE tasks SET traceparent=? WHERE id=?",
            "00-" + ctx.traceId() + "-" + ctx.spanId() + "-01",
            id);
      }
      event(id, tenant, 1, "task_created", Map.of("batch_id", batch));
      outbox(id, 1);
    }
    return Map.of("batch_id", batch, "reused", false);
  }

  public List<Map<String, Object>> batches(String tenant, String before, int limit) {
    return db.queryForList(
        "SELECT id,title,item_count,created_at FROM batches WHERE tenant_id=? AND (?='' OR id<?) ORDER BY id DESC LIMIT ?",
        tenant,
        before,
        before,
        limit);
  }

  public List<Map<String, Object>> tasks(String tenant, String batch, String before, int limit) {
    return tasks(tenant, batch, before, limit, false);
  }

  public List<Map<String, Object>> tasks(
      String tenant, String batch, String before, int limit, boolean review) {
    String filter = review ? " AND status='COMPLETED' AND review_status='PENDING'" : "";
    return db.queryForList(
        "SELECT id,batch_id,ticker,fiscal_year,metric,status,step,revision,machine_verdict,review_status,error_code,dispatches,created_at,updated_at FROM tasks WHERE tenant_id=?"
            + filter
            + " AND (?='' OR batch_id=?) AND (?='' OR id<?) ORDER BY id DESC LIMIT ?",
        tenant,
        batch,
        batch,
        before,
        before,
        limit);
  }

  public Map<String, Object> detail(String id, String tenant) {
    var t = new LinkedHashMap<>(task(id, tenant, false));
    t.remove("lease_token");
    t.remove("lease_until");
    t.remove("checkpoint");
    for (String key : List.of("request", "result", "evidence"))
      if (t.get(key) != null) t.put(key, Json.map(t.get(key)));
    t.put(
        "attempts",
        db.queryForList(
            "SELECT id,status,reserved_micros,actual_micros,created_at,settled_at FROM attempts WHERE task_id=? ORDER BY created_at,id",
            id));
    t.put(
        "reviews",
        db.queryForList(
            "SELECT reviewer_id,decision,reason,result_revision,created_at FROM reviews WHERE task_id=? ORDER BY created_at",
            id));
    return t;
  }

  @Transactional
  public Map<String, Object> claim(String id, Contracts.Claim req) {
    var rows = db.queryForList("SELECT * FROM tasks WHERE id=? FOR UPDATE", id);
    if (rows.isEmpty()) throw new Problem(404, "task_not_found");
    var t = rows.getFirst();
    if (!"READY".equals(t.get("status")) || Json.number(t, "revision") != req.expected_revision())
      throw Problem.conflict("not_claimable");
    String token = Json.id();
    long rev = req.expected_revision() + 1;
    db.update(
        "UPDATE tasks SET status='RUNNING',revision=?,lease_token=?,lease_until=?,updated_at=? WHERE id=?",
        rev,
        token,
        Json.now() + leaseMillis,
        Json.now(),
        id);
    event(
        id,
        t.get("tenant_id").toString(),
        rev,
        "step_claimed",
        Map.of("step", t.get("step"), "worker_id", req.worker_id()));
    Map<String, Object> output = new LinkedHashMap<>();
    output.put("protocol_version", 1);
    output.put("task_id", id);
    output.put("tenant_id", t.get("tenant_id"));
    output.put("revision", rev);
    output.put("lease_token", token);
    output.put("step", t.get("step"));
    output.put("traceparent", t.get("traceparent"));
    metrics
        .timer("finreceipts_queue_wait", "step", t.get("step").toString())
        .record(
            Math.max(0, Json.now() - Json.number(t, "updated_at")),
            java.util.concurrent.TimeUnit.MILLISECONDS);
    output.put("request", Json.map(t.get("request")));
    output.put("checkpoint", Json.map(t.get("checkpoint")));
    output.put("evidence", t.get("evidence") == null ? null : Json.map(t.get("evidence")));
    output.put(
        "profile",
        Json.map(
            db.queryForObject(
                "SELECT document FROM execution_profiles WHERE id=?",
                String.class,
                t.get("profile_id"))));
    return output;
  }

  @Transactional
  public void heartbeat(String id, String token) {
    workerTask(id, token);
    db.update("UPDATE tasks SET lease_until=? WHERE id=?", Json.now() + leaseMillis, id);
  }

  @Transactional
  public Map<String, Object> reserve(String id, Contracts.Reservation req) {
    // Fixed lock order: global budget -> tenant -> task. Settlement uses the same order.
    var global = db.queryForMap("SELECT * FROM provider_budget WHERE id='global' FOR UPDATE");
    var taskTenant = db.queryForList("SELECT tenant_id FROM tasks WHERE id=?", id);
    if (taskTenant.isEmpty()) throw new Problem(404, "task_not_found");
    String tenant = taskTenant.getFirst().get("tenant_id").toString();
    var quota = db.queryForMap("SELECT * FROM tenants WHERE id=? FOR UPDATE", tenant);
    var t = workerTask(id, req.lease_token());
    if (!"MODEL".equals(t.get("step"))) throw Problem.conflict("not_model_step");
    var old = db.queryForList("SELECT * FROM attempts WHERE id=?", req.attempt_id());
    if (!old.isEmpty()) {
      var a = old.getFirst();
      if (!id.equals(a.get("task_id"))
          || !req.lease_token().equals(a.get("lease_token"))
          || req.reserve_micros() != Json.number(a, "reserved_micros"))
        throw Problem.conflict("attempt_conflict");
      // A lost reserve response is ambiguous: never dispatch twice using the same attempt.
      throw Problem.conflict("attempt_already_reserved");
    }
    if (Json.number(t, "dispatches") >= 6) throw Problem.conflict("attempt_limit");
    long active =
        db.queryForObject(
            "SELECT COUNT(*) FROM attempts WHERE status IN ('RESERVED','UNKNOWN')", Long.class);
    if (active >= modelConcurrency) throw new Problem(429, "provider_capacity");
    for (var q : List.of(global, quota)) {
      long ceiling = Json.number(q, q == global ? "limit_micros" : "budget_micros");
      if (Json.number(q, "spent_micros") + Json.number(q, "reserved_micros") + req.reserve_micros()
          > ceiling) throw new Problem(429, "budget_exhausted");
    }
    db.update(
        "INSERT INTO attempts(id,task_id,tenant_id,lease_token,status,reserved_micros,created_at) VALUES (?,?,?,?,'RESERVED',?,?)",
        req.attempt_id(),
        id,
        tenant,
        req.lease_token(),
        req.reserve_micros(),
        Json.now());
    db.update(
        "UPDATE provider_budget SET reserved_micros=reserved_micros+? WHERE id='global'",
        req.reserve_micros());
    db.update(
        "UPDATE tenants SET reserved_micros=reserved_micros+? WHERE id=?",
        req.reserve_micros(),
        tenant);
    db.update("UPDATE tasks SET dispatches=dispatches+1 WHERE id=?", id);
    return Map.of("attempt_id", req.attempt_id(), "reserved_micros", req.reserve_micros());
  }

  @Transactional
  public void settle(String id, Contracts.Settlement req) {
    db.queryForMap("SELECT * FROM provider_budget WHERE id='global' FOR UPDATE");
    var rows =
        db.queryForList("SELECT * FROM attempts WHERE id=? AND task_id=?", req.attempt_id(), id);
    if (rows.isEmpty()) throw new Problem(404, "attempt_not_found");
    var a = rows.getFirst();
    db.queryForMap("SELECT * FROM tenants WHERE id=? FOR UPDATE", a.get("tenant_id"));
    if (!req.lease_token().equals(a.get("lease_token"))) throw Problem.conflict("attempt_fenced");
    if ("SETTLED".equals(a.get("status"))) {
      if (req.actual_micros() != Json.number(a, "actual_micros")
          || !Json.map(a.get("usage_json")).equals(req.usage()))
        throw Problem.conflict("settlement_conflict");
      return;
    }
    // Late usage may settle the bill, but can never commit a cancelled task's answer.
    long reserved = Json.number(a, "reserved_micros");
    if (req.actual_micros() > reserved) throw Problem.conflict("cost_exceeds_reservation");
    db.update(
        "UPDATE attempts SET status='SETTLED',actual_micros=?,usage_json=?,settled_at=? WHERE id=?",
        req.actual_micros(),
        Json.write(req.usage()),
        Json.now(),
        req.attempt_id());
    db.update(
        "UPDATE provider_budget SET reserved_micros=reserved_micros-?,spent_micros=spent_micros+? WHERE id='global'",
        reserved,
        req.actual_micros());
    db.update(
        "UPDATE tenants SET reserved_micros=reserved_micros-?,spent_micros=spent_micros+? WHERE id=?",
        reserved,
        req.actual_micros(),
        a.get("tenant_id"));
    metrics
        .timer("finreceipts_model_attempt_duration")
        .record(
            Math.max(0, Json.now() - Json.number(a, "created_at")),
            java.util.concurrent.TimeUnit.MILLISECONDS);
  }

  @Transactional
  public Map<String, Object> commit(String id, Contracts.Commit req) {
    String digest = Json.hash(req);
    var done =
        db.queryForList(
            "SELECT digest,revision FROM commits WHERE task_id=? AND lease_token=?",
            id,
            req.lease_token());
    if (!done.isEmpty()) {
      if (!digest.equals(done.getFirst().get("digest"))) throw Problem.conflict("commit_conflict");
      return Map.of("revision", done.getFirst().get("revision"), "reused", true);
    }
    var t = workerTask(id, req.lease_token());
    long rev = Json.number(t, "revision") + 1;
    if (req.expected_revision() != rev - 1) throw Problem.conflict("revision_conflict");
    String step = t.get("step").toString();
    boolean valid =
        switch (step) {
          case "PREPARE" -> Set.of("MODEL", "DONE").contains(req.next_step());
          case "MODEL" -> Set.of("MODEL", "VERIFY").contains(req.next_step());
          case "VERIFY" -> Set.of("MODEL", "DONE").contains(req.next_step());
          default -> false;
        };
    if (!valid) throw Problem.conflict("invalid_transition");
    if ("PREPARE".equals(step) && req.evidence() == null)
      throw Problem.conflict("evidence_required");
    if (!"PREPARE".equals(step) && req.evidence() != null)
      throw Problem.conflict("evidence_is_frozen");
    long unknown =
        db.queryForObject(
            "SELECT COUNT(*) FROM attempts WHERE task_id=? AND status IN ('RESERVED','UNKNOWN')",
            Long.class,
            id);
    if (unknown > 0) throw Problem.conflict("unsettled_attempt");
    boolean terminal = "DONE".equals(req.next_step());
    if (terminal
        && (req.result() == null
            || req.machine_verdict() == null
            || "PENDING".equals(req.machine_verdict()))) throw Problem.conflict("result_required");
    if (terminal && "SUPPORTED".equals(req.machine_verdict())) {
      var evidence =
          req.evidence() != null
              ? req.evidence()
              : (t.get("evidence") == null ? Map.of() : Json.map(t.get("evidence")));
      if (evidence.isEmpty()) throw Problem.conflict("supported_requires_evidence");
    }
    if (Json.write(req.checkpoint()).length() > 200000)
      throw new IllegalArgumentException("checkpoint_too_large");
    db.update(
        "UPDATE tasks SET status=?,step=?,revision=?,checkpoint=?,result=?,machine_verdict=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
        terminal ? "COMPLETED" : "QUEUED",
        req.next_step(),
        rev,
        Json.write(req.checkpoint()),
        req.result() == null ? null : Json.write(req.result()),
        req.machine_verdict() == null ? "PENDING" : req.machine_verdict(),
        Json.now(),
        id);
    if (req.evidence() != null) {
      db.update(
          "UPDATE tasks SET evidence=?,evidence_digest=? WHERE id=?",
          Json.write(req.evidence()),
          Json.hash(req.evidence()),
          id);
      artifact("evidence", req.evidence());
    }
    event(
        id,
        t.get("tenant_id").toString(),
        rev,
        terminal ? "task_completed" : "step_committed",
        Map.of("next_step", req.next_step()));
    if (!terminal) outbox(id, rev);
    if (terminal)
      metrics
          .timer("finreceipts_task_duration", "verdict", req.machine_verdict())
          .record(
              Math.max(0, Json.now() - Json.number(t, "created_at")),
              java.util.concurrent.TimeUnit.MILLISECONDS);
    db.update(
        "INSERT INTO commits(task_id,lease_token,digest,revision) VALUES (?,?,?,?)",
        id,
        req.lease_token(),
        digest,
        rev);
    return Map.of("revision", rev, "reused", false);
  }

  @Transactional
  public void fail(String id, Contracts.Failure req) {
    var t = workerTask(id, req.lease_token());
    long active =
        db.queryForObject(
            "SELECT COUNT(*) FROM attempts WHERE task_id=? AND status='RESERVED'", Long.class, id);
    boolean unknown = req.unknown() || active > 0;
    long rev = Json.number(t, "revision") + 1;
    db.update("UPDATE attempts SET status='UNKNOWN' WHERE task_id=? AND status='RESERVED'", id);
    db.update(
        "UPDATE tasks SET status=?,error_code=?,revision=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
        unknown ? "UNKNOWN" : "FAILED",
        req.code(),
        rev,
        Json.now(),
        id);
    event(
        id,
        t.get("tenant_id").toString(),
        rev,
        "task_failed",
        Map.of("unknown", unknown, "code", req.code()));
  }

  @Transactional
  public void change(String id, Map<String, Object> m, Contracts.Revision req, boolean cancel) {
    role(m, "ADMIN", "ANALYST");
    var t = task(id, m.get("tenant_id").toString(), true);
    if (!m.get("id").equals(t.get("creator_id")) && !"ADMIN".equals(m.get("role")))
      throw new Problem(403, "owner_required");
    if (Json.number(t, "revision") != req.expected_revision())
      throw Problem.conflict("revision_conflict");
    if (cancel && Set.of("COMPLETED", "CANCELLED").contains(t.get("status")))
      throw Problem.conflict("terminal_task");
    if (!cancel && !Set.of("FAILED", "UNKNOWN").contains(t.get("status")))
      throw Problem.conflict("not_retryable");
    if (!cancel && "UNKNOWN".equals(t.get("status")) && !req.acknowledge_possible_duplicate())
      throw Problem.conflict("acknowledgment_required");
    db.update("UPDATE attempts SET status='UNKNOWN' WHERE task_id=? AND status='RESERVED'", id);
    // Unknown reservations stay charged. They do not silently become free capacity.
    if (!cancel && req.acknowledge_possible_duplicate())
      db.update(
          "UPDATE attempts SET status='ACKNOWLEDGED_UNKNOWN' WHERE task_id=? AND status='UNKNOWN'",
          id);
    long rev = req.expected_revision() + 1;
    db.update(
        "UPDATE tasks SET status=?,revision=?,lease_token=NULL,lease_until=NULL,error_code=NULL,updated_at=? WHERE id=?",
        cancel ? "CANCELLED" : "QUEUED",
        rev,
        Json.now(),
        id);
    event(
        id,
        t.get("tenant_id").toString(),
        rev,
        cancel ? "task_cancelled" : "retry_requested",
        Map.of("possible_duplicate_acknowledged", req.acknowledge_possible_duplicate()));
    if (!cancel) outbox(id, rev);
  }

  @Transactional
  public void review(String id, Map<String, Object> m, Contracts.Review req) {
    role(m, "ADMIN", "REVIEWER");
    var t = task(id, m.get("tenant_id").toString(), true);
    if (m.get("id").equals(t.get("creator_id"))) throw new Problem(403, "self_review_denied");
    if (!"COMPLETED".equals(t.get("status"))
        || Json.number(t, "revision") != req.expected_revision()
        || !"PENDING".equals(t.get("review_status"))) throw Problem.conflict("review_conflict");
    db.update(
        "INSERT INTO reviews(id,task_id,tenant_id,reviewer_id,result_revision,decision,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
        Json.id(),
        id,
        m.get("tenant_id"),
        m.get("id"),
        req.expected_revision(),
        req.decision(),
        req.reason(),
        Json.now());
    long rev = req.expected_revision() + 1;
    db.update(
        "UPDATE tasks SET review_status=?,revision=?,updated_at=? WHERE id=?",
        req.decision(),
        rev,
        Json.now(),
        id);
    event(
        id,
        t.get("tenant_id").toString(),
        rev,
        "review_recorded",
        Map.of("decision", req.decision(), "reviewer_id", m.get("id"), "reason", req.reason()));
  }

  public Map<String, Object> quota(String tenant) {
    var q = new LinkedHashMap<>(db.queryForMap("SELECT * FROM tenants WHERE id=?", tenant));
    q.put(
        "states",
        db.queryForList(
            "SELECT status,COUNT(*) count FROM tasks WHERE tenant_id=? GROUP BY status", tenant));
    return q;
  }

  public List<Map<String, Object>> events(String id, String tenant, long after) {
    task(id, tenant, false);
    return db.queryForList(
        "SELECT seq,kind,document,created_at FROM events WHERE task_id=? AND tenant_id=? AND seq>? ORDER BY seq LIMIT 100",
        id,
        tenant,
        after);
  }

  public Map<String, Object> export(String batch, String tenant) {
    if (db.queryForList("SELECT id FROM batches WHERE id=? AND tenant_id=?", batch, tenant)
        .isEmpty()) throw new Problem(404, "batch_not_found");
    var ids =
        db.queryForList(
            "SELECT id FROM tasks WHERE batch_id=? AND tenant_id=? AND review_status='ACCEPTED' ORDER BY id",
            String.class,
            batch,
            tenant);
    var items = ids.stream().map(id -> detail(id, tenant)).toList();
    var delivery =
        Map.of(
            "schema_version",
            1,
            "batch_id",
            batch,
            "approved_items",
            items,
            "digest",
            Json.hash(items),
            "coverage",
            "Supported numeric targets only; human review does not change machine verdicts.");
    artifact("export", delivery);
    return delivery;
  }
}
