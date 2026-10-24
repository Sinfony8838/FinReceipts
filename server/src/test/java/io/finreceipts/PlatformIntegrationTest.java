package io.finreceipts;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.*;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

@Testcontainers
@SpringBootTest(
    properties = {
      "finreceipts.scheduler-enabled=false",
      "management.tracing.enabled=false",
      "finreceipts.worker-secret=integration-only-worker-secret-00000000000000",
      "finreceipts.bootstrap-password=integration-only-password-0000000"
    })
@AutoConfigureMockMvc
class PlatformIntegrationTest {
  @Container static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4");

  @Container
  static final GenericContainer<?> REDIS =
      new GenericContainer<>("redis:8-alpine").withExposedPorts(6379);

  @Container
  static final GenericContainer<?> RABBIT =
      new GenericContainer<>("rabbitmq:4-alpine").withExposedPorts(5672);

  @DynamicPropertySource
  static void properties(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", MYSQL::getJdbcUrl);
    r.add("spring.datasource.username", MYSQL::getUsername);
    r.add("spring.datasource.password", MYSQL::getPassword);
    r.add("spring.data.redis.host", REDIS::getHost);
    r.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
    r.add("spring.rabbitmq.host", RABBIT::getHost);
    r.add("spring.rabbitmq.port", () -> RABBIT.getMappedPort(5672));
    r.add("spring.rabbitmq.username", () -> "guest");
    r.add("spring.rabbitmq.password", () -> "guest");
  }

  @Autowired PlatformService service;
  @Autowired MockMvc mvc;
  @Autowired TransactionTemplate tx;
  @Autowired RabbitTemplate rabbit;
  @Autowired MeterRegistry metrics;

  Map<String, Object> analyst() {
    return service.member("alpha.analyst");
  }

  Contracts.Batch body(String title) {
    return new Contracts.Batch(
        title,
        List.of(
            new Contracts.Item(
                "metric", "AAPL", new Contracts.Target("direct", "revenue", 2023, null), null)));
  }

  String task() {
    var m = analyst();
    String b = service.create(m, Json.id(), body("test")).get("batch_id").toString();
    return service.tasks("alpha", b, "", 10).getFirst().get("id").toString();
  }

  Map<String, Object> claim(String id) {
    service.jdbc().update("UPDATE tasks SET status='READY' WHERE id=?", id);
    return service.claim(
        id,
        new Contracts.Claim(
            Json.number(service.task(id, "alpha", false), "revision"), "test-worker"));
  }

  void done(String id, Map<String, Object> c) {
    service.commit(
        id,
        new Contracts.Commit(
            c.get("lease_token").toString(),
            Json.number(c, "revision"),
            "DONE",
            Map.of(),
            Map.of(),
            Map.of("answer", "ANSWER: N/A"),
            "UNSUPPORTED"));
  }

  @BeforeEach
  void clear() {
    for (String table :
        List.of("reviews", "commits", "events", "outbox", "attempts", "tasks", "batches"))
      service.jdbc().update("DELETE FROM " + table);
    service
        .jdbc()
        .update(
            "UPDATE tenants SET reserved_micros=0,spent_micros=0,max_pending=500,max_running=4");
    service
        .jdbc()
        .update(
            "UPDATE provider_budget SET reserved_micros=0,spent_micros=0,limit_micros=20000000");
  }

  @Test
  void oneHundredConcurrentCreatesAreOneObject() throws Exception {
    var pool = Executors.newFixedThreadPool(16);
    String key = Json.id();
    var m = analyst();
    try {
      var work = new ArrayList<Callable<String>>();
      for (int i = 0; i < 100; i++)
        work.add(() -> service.create(m, key, body("parallel")).get("batch_id").toString());
      var ids = new HashSet<String>();
      for (var f : pool.invokeAll(work)) ids.add(f.get());
      assertEquals(1, ids.size());
      assertEquals(1, service.jdbc().queryForObject("SELECT COUNT(*) FROM batches", Integer.class));
      assertEquals(1, service.jdbc().queryForObject("SELECT COUNT(*) FROM events", Integer.class));
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  void sameKeyChangedBodyConflicts() {
    String key = Json.id();
    service.create(analyst(), key, body("a"));
    assertEquals(
        409, assertThrows(Problem.class, () -> service.create(analyst(), key, body("b"))).status);
  }

  @Test
  void tenantAndReviewIsolation() {
    String id = task();
    var c = claim(id);
    done(id, c);
    long r = Json.number(service.task(id, "alpha", false), "revision");
    assertEquals(404, assertThrows(Problem.class, () -> service.detail(id, "beta")).status);
    assertEquals(
        404,
        assertThrows(
                Problem.class,
                () ->
                    service.review(
                        id,
                        service.member("beta.reviewer"),
                        new Contracts.Review(r, "ACCEPTED", "checked")))
            .status);
    var self = new HashMap<>(analyst());
    self.put("role", "REVIEWER");
    assertEquals(
        403,
        assertThrows(
                Problem.class,
                () -> service.review(id, self, new Contracts.Review(r, "ACCEPTED", "checked")))
            .status);
  }

  @Test
  void reviewDoesNotChangeMachineVerdict() {
    String id = task();
    done(id, claim(id));
    long r = Json.number(service.task(id, "alpha", false), "revision");
    service.review(
        id,
        service.member("alpha.reviewer"),
        new Contracts.Review(r, "ACCEPTED", "Accept the explicit evidence-missing refusal."));
    var d = service.detail(id, "alpha");
    assertEquals("UNSUPPORTED", d.get("machine_verdict"));
    assertEquals("ACCEPTED", d.get("review_status"));
    assertEquals(
        1,
        ((List<?>) service.export(d.get("batch_id").toString(), "alpha").get("approved_items"))
            .size());
  }

  @Test
  void cancelledWorkerCannotCommit() {
    String id = task();
    var c = claim(id);
    service.change(id, analyst(), new Contracts.Revision(Json.number(c, "revision"), false), true);
    assertEquals("stale_lease", assertThrows(Problem.class, () -> done(id, c)).getMessage());
  }

  @Test
  void commitResponseLossIsIdempotent() {
    String id = task();
    var c = claim(id);
    done(id, c);
    done(id, c);
    assertEquals(3, Json.number(service.task(id, "alpha", false), "revision"));
    assertEquals(
        3,
        service
            .jdbc()
            .queryForObject("SELECT COUNT(*) FROM events WHERE task_id=?", Integer.class, id));
  }

  @Test
  void reservationsAndSettlementAreDurable() {
    String id = task();
    var c = claim(id);
    String token = c.get("lease_token").toString();
    service.jdbc().update("UPDATE tasks SET step='MODEL' WHERE id=?", id);
    String attempt = Json.id();
    service.reserve(id, new Contracts.Reservation(token, attempt, 200));
    assertThrows(
        Problem.class, () -> service.reserve(id, new Contracts.Reservation(token, attempt, 200)));
    assertEquals(
        200,
        service
            .jdbc()
            .queryForObject(
                "SELECT reserved_micros FROM provider_budget WHERE id='global'", Long.class));
    var settlement = new Contracts.Settlement(token, attempt, 100, Map.of("output_tokens", 1));
    service.settle(id, settlement);
    service.settle(id, settlement);
    assertEquals(
        100,
        service
            .jdbc()
            .queryForObject(
                "SELECT spent_micros FROM provider_budget WHERE id='global'", Long.class));
    assertEquals(
        0,
        service
            .jdbc()
            .queryForObject(
                "SELECT reserved_micros FROM provider_budget WHERE id='global'", Long.class));
  }

  @Test
  void unknownRecoveryRetainsBudget() {
    String id = task();
    var c = claim(id);
    service.jdbc().update("UPDATE tasks SET step='MODEL' WHERE id=?", id);
    service.reserve(id, new Contracts.Reservation(c.get("lease_token").toString(), Json.id(), 200));
    service.jdbc().update("UPDATE tasks SET lease_until=0 WHERE id=?", id);
    new Scheduler(service, tx, rabbit, metrics, true).recover();
    var t = service.task(id, "alpha", false);
    assertEquals("UNKNOWN", t.get("status"));
    assertEquals(
        200,
        service
            .jdbc()
            .queryForObject(
                "SELECT reserved_micros FROM provider_budget WHERE id='global'", Long.class));
    assertThrows(
        Problem.class,
        () ->
            service.change(
                id, analyst(), new Contracts.Revision(Json.number(t, "revision"), false), false));
  }

  @Test
  void completedModelWithoutCheckpointStillNeedsConfirmation() {
    String id = task();
    var c = claim(id);
    service.jdbc().update("UPDATE tasks SET step='MODEL' WHERE id=?", id);
    String attempt = Json.id();
    service.reserve(id, new Contracts.Reservation(c.get("lease_token").toString(), attempt, 200));
    service.settle(
        id, new Contracts.Settlement(c.get("lease_token").toString(), attempt, 100, Map.of()));
    service.jdbc().update("UPDATE tasks SET lease_until=0 WHERE id=?", id);
    new Scheduler(service, tx, rabbit, metrics, true).recover();
    assertEquals("UNKNOWN", service.task(id, "alpha", false).get("status"));
  }

  @Test
  void undispatchedExpiredStepCanRecover() {
    String id = task();
    claim(id);
    service.jdbc().update("UPDATE tasks SET lease_until=0 WHERE id=?", id);
    new Scheduler(service, tx, rabbit, metrics, true).recover();
    assertEquals("QUEUED", service.task(id, "alpha", false).get("status"));
  }

  @Test
  void csrfIsEnforcedAndReadCannotTriggerWork() throws Exception {
    mvc.perform(
            post("/api/v2/batches")
                .with(user("alpha.analyst").roles("ANALYST"))
                .header("Idempotency-Key", "csrf-test")
                .contentType("application/json")
                .content(Json.write(body("test"))))
        .andExpect(status().isForbidden());
    assertEquals(0, service.jdbc().queryForObject("SELECT COUNT(*) FROM batches", Integer.class));
    mvc.perform(get("/api/v2/tasks").with(user("alpha.analyst").roles("ANALYST")))
        .andExpect(status().isOk());
    assertEquals(0, service.jdbc().queryForObject("SELECT COUNT(*) FROM attempts", Integer.class));
  }

  @Test
  void queueSaturationDoesNotCreateOutbox() {
    service.jdbc().update("UPDATE tenants SET max_pending=0 WHERE id='alpha'");
    assertEquals(
        429,
        assertThrows(Problem.class, () -> service.create(analyst(), Json.id(), body("full")))
            .status);
    assertEquals(0, service.jdbc().queryForObject("SELECT COUNT(*) FROM outbox", Integer.class));
  }

  @Test
  void authenticatedBearerCanWriteWithoutCsrfButCannotReview() throws Exception {
    String token = "integration-token";
    service
        .jdbc()
        .update(
            "INSERT INTO api_tokens(digest,member_id,scope,expires_at) VALUES (?,?,?,?) ON DUPLICATE KEY UPDATE expires_at=VALUES(expires_at)",
            Json.hashBytes(token.getBytes()),
            analyst().get("id"),
            "submit",
            Json.now() + 60000);
    mvc.perform(
            post("/api/v2/batches")
                .header("Authorization", "Bearer " + token)
                .header("Idempotency-Key", Json.id())
                .contentType("application/json")
                .content(Json.write(body("token"))))
        .andExpect(status().isAccepted());
    mvc.perform(
            post("/api/v2/tasks/absent/review")
                .header("Authorization", "Bearer " + token)
                .contentType("application/json")
                .content(
                    "{\"expected_revision\":1,\"decision\":\"ACCEPTED\",\"reason\":\"checked\"}"))
        .andExpect(status().isForbidden());
  }

  @Test
  void unknownRetryDoesNotRefundReservation() {
    String id = task();
    var c = claim(id);
    service.jdbc().update("UPDATE tasks SET step='MODEL' WHERE id=?", id);
    service.reserve(id, new Contracts.Reservation(c.get("lease_token").toString(), Json.id(), 200));
    service.fail(
        id, new Contracts.Failure(c.get("lease_token").toString(), "transport_unknown", true));
    service.change(
        id,
        analyst(),
        new Contracts.Revision(Json.number(service.task(id, "alpha", false), "revision"), true),
        false);
    assertEquals(
        200,
        service
            .jdbc()
            .queryForObject(
                "SELECT reserved_micros FROM provider_budget WHERE id='global'", Long.class));
    assertEquals(
        "ACKNOWLEDGED_UNKNOWN",
        service
            .jdbc()
            .queryForObject("SELECT status FROM attempts WHERE task_id=?", String.class, id));
  }

  @Test
  void publishedButUnconfirmedDeliveryClaimsOnlyOnce() {
    String id = task();
    var unreliable = org.mockito.Mockito.mock(RabbitTemplate.class);
    org.mockito.Mockito.doAnswer(
            invocation -> {
              rabbit.convertAndSend(
                  "", "finreceipts.v2.steps", invocation.getArgument(2, String.class));
              throw new org.springframework.amqp.AmqpException("simulated_confirm_loss");
            })
        .when(unreliable)
        .convertAndSend(
            org.mockito.ArgumentMatchers.anyString(),
            org.mockito.ArgumentMatchers.anyString(),
            org.mockito.ArgumentMatchers.any(Object.class),
            org.mockito.ArgumentMatchers.any(
                org.springframework.amqp.rabbit.connection.CorrelationData.class));
    var scheduler = new Scheduler(service, tx, unreliable, metrics, true);
    assertEquals(Boolean.TRUE, tx.execute(s -> scheduler.dispatchOne()));
    assertThrows(IllegalStateException.class, scheduler::publishOne);
    assertEquals("READY", service.task(id, "alpha", false).get("status"));
    assertEquals(
        0,
        service
            .jdbc()
            .queryForObject(
                "SELECT COUNT(*) FROM outbox WHERE task_id=? AND published=true",
                Integer.class,
                id));
    assertNotNull(rabbit.receiveAndConvert("finreceipts.v2.steps", 5000));
    assertNotNull(service.claim(id, new Contracts.Claim(2, "first-delivery")));
    assertThrows(
        Problem.class, () -> service.claim(id, new Contracts.Claim(2, "duplicate-delivery")));
  }

  @Test
  void committedDispatchRecoversWhenPublisherNeverRuns() {
    String id = task();
    var scheduler = new Scheduler(service, tx, rabbit, metrics, true);
    assertEquals(Boolean.TRUE, tx.execute(s -> scheduler.dispatchOne()));
    assertEquals("READY", service.task(id, "alpha", false).get("status"));
    service.jdbc().update("UPDATE tasks SET lease_until=0 WHERE id=?", id);
    scheduler.recover();
    assertEquals("QUEUED", service.task(id, "alpha", false).get("status"));
    assertEquals(3L, Json.number(service.task(id, "alpha", false), "revision"));
    assertThrows(Problem.class, () -> service.claim(id, new Contracts.Claim(2, "late-delivery")));
    assertEquals(
        1,
        service
            .jdbc()
            .queryForObject(
                "SELECT COUNT(*) FROM outbox WHERE task_id=? AND revision=3 AND published=false",
                Integer.class,
                id));
  }
}
