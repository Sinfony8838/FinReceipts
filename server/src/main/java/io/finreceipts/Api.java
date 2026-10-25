package io.finreceipts;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import java.nio.charset.StandardCharsets;
import java.security.Principal;
import java.security.SecureRandom;
import java.util.*;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/v2")
class Api {
  final PlatformService service;
  final RateControl rate;
  final PasswordEncoder passwords;

  Api(PlatformService service, RateControl rate, PasswordEncoder passwords) {
    this.service = service;
    this.rate = rate;
    this.passwords = passwords;
  }

  Map<String, Object> identity(Principal p, HttpServletRequest req, boolean write) {
    var m = service.member(p.getName());
    Object scope = req.getAttribute("tokenScope");
    if (write && scope != null && !"submit".equals(scope))
      throw new Problem(403, "token_scope_denied");
    rate.acquire(m.get("tenant_id").toString(), 250);
    return m;
  }

  Map<String, Object> interactive(Principal p, HttpServletRequest req) {
    var m = identity(p, req, true);
    if (req.getAttribute("tokenScope") != null) throw new Problem(403, "session_required");
    return m;
  }

  @GetMapping("/csrf")
  Map<String, String> csrf(CsrfToken token) {
    return Map.of("token", token.getToken(), "header", token.getHeaderName());
  }

  @GetMapping("/me")
  Map<String, Object> me(Principal p, HttpServletRequest req) {
    return identity(p, req, false);
  }

  @PostMapping("/batches")
  ResponseEntity<?> create(
      @RequestHeader("Idempotency-Key") String key,
      @Valid @RequestBody Contracts.Batch body,
      Principal p,
      HttpServletRequest req) {
    return ResponseEntity.accepted().body(service.create(identity(p, req, true), key, body));
  }

  @GetMapping("/batches")
  Map<String, Object> batches(
      @RequestParam(defaultValue = "") String before,
      @RequestParam(defaultValue = "30") int limit,
      Principal p,
      HttpServletRequest req) {
    var m = identity(p, req, false);
    var rows = service.batches(m.get("tenant_id").toString(), before, limit(limit));
    return page(rows, limit);
  }

  static int limit(int limit) {
    if (limit < 1 || limit > 100) throw new IllegalArgumentException("page_limit");
    return limit;
  }

  static Map<String, Object> page(List<Map<String, Object>> rows, int limit) {
    return Map.of(
        "items", rows, "next_cursor", rows.size() == limit ? rows.getLast().get("id") : "");
  }

  @GetMapping("/tasks")
  Map<String, Object> tasks(
      @RequestParam(defaultValue = "") String batch_id,
      @RequestParam(defaultValue = "") String before,
      @RequestParam(defaultValue = "50") int limit,
      @RequestParam(defaultValue = "false") boolean review,
      Principal p,
      HttpServletRequest req) {
    var m = identity(p, req, false);
    return page(
        service.tasks(m.get("tenant_id").toString(), batch_id, before, limit(limit), review),
        limit);
  }

  @GetMapping("/tasks/{id}")
  Map<String, Object> detail(@PathVariable String id, Principal p, HttpServletRequest req) {
    return service.detail(id, identity(p, req, false).get("tenant_id").toString());
  }

  @PostMapping("/tasks/{id}/cancel")
  Map<String, Boolean> cancel(
      @PathVariable String id,
      @Valid @RequestBody Contracts.Revision body,
      Principal p,
      HttpServletRequest req) {
    service.change(id, identity(p, req, true), body, true);
    return Map.of("ok", true);
  }

  @PostMapping("/tasks/{id}/retry")
  Map<String, Boolean> retry(
      @PathVariable String id,
      @Valid @RequestBody Contracts.Revision body,
      Principal p,
      HttpServletRequest req) {
    service.change(id, identity(p, req, true), body, false);
    return Map.of("ok", true);
  }

  @PostMapping("/tasks/{id}/review")
  Map<String, Boolean> review(
      @PathVariable String id,
      @Valid @RequestBody Contracts.Review body,
      Principal p,
      HttpServletRequest req) {
    service.review(id, interactive(p, req), body);
    return Map.of("ok", true);
  }

  @GetMapping("/quota")
  Map<String, Object> quota(Principal p, HttpServletRequest req) {
    return service.quota(identity(p, req, false).get("tenant_id").toString());
  }

  @GetMapping("/batches/{id}/export")
  Map<String, Object> export(@PathVariable String id, Principal p, HttpServletRequest req) {
    return service.export(id, identity(p, req, false).get("tenant_id").toString());
  }

  @GetMapping(value = "/tasks/{id}/events", produces = "text/event-stream")
  SseEmitter events(
      @PathVariable String id,
      @RequestHeader(value = "Last-Event-ID", required = false) Long last,
      @RequestParam(defaultValue = "0") long after,
      Principal p,
      HttpServletRequest req) {
    var m = identity(p, req, false);
    String tenant = m.get("tenant_id").toString();
    long cursor = last == null ? after : last;
    var task = service.task(id, tenant, false);
    if (cursor < 0 || cursor > Json.number(task, "revision"))
      throw Problem.conflict("invalid_event_cursor");
    String permit = rate.openStream(tenant);
    var emitter = new SseEmitter(30000L);
    var closed = new java.util.concurrent.atomic.AtomicBoolean();
    Runnable close =
        () -> {
          if (closed.compareAndSet(false, true)) rate.closeStream(tenant, permit);
        };
    emitter.onCompletion(close);
    emitter.onTimeout(close);
    emitter.onError(e -> close.run());
    Thread.startVirtualThread(
        () -> {
          long position = cursor, until = Json.now() + 28000;
          try {
            while (!closed.get() && Json.now() < until) {
              // Identity is revalidated throughout the stream, including disabled memberships.
              var current = service.member(p.getName());
              if (!tenant.equals(current.get("tenant_id"))) break;
              for (var event : service.events(id, tenant, position)) {
                position = Json.number(event, "seq");
                emitter.send(
                    SseEmitter.event()
                        .id(Long.toString(position))
                        .name(event.get("kind").toString())
                        .data(Json.map(event.get("document"))));
              }
              emitter.send(SseEmitter.event().comment("heartbeat"));
              Thread.sleep(1000);
            }
            emitter.complete();
          } catch (Exception e) {
            emitter.completeWithError(e);
          } finally {
            close.run();
          }
        });
    return emitter;
  }

  @PostMapping("/members")
  Map<String, String> member(
      @Valid @RequestBody Contracts.Member body, Principal p, HttpServletRequest req) {
    var m = interactive(p, req);
    PlatformService.role(m, "ADMIN");
    String id = Json.id();
    service
        .jdbc()
        .update(
            "INSERT INTO members(id,tenant_id,username,password_hash,role) VALUES (?,?,?,?,?)",
            id,
            m.get("tenant_id"),
            body.username(),
            passwords.encode(body.password()),
            body.role());
    return Map.of("id", id);
  }

  @PostMapping("/tokens")
  Map<String, String> token(
      @Valid @RequestBody Contracts.Token body, Principal p, HttpServletRequest req) {
    var m = identity(p, req, true);
    if (req.getAttribute("tokenScope") != null) throw new Problem(403, "session_required");
    byte[] bytes = new byte[32];
    new SecureRandom().nextBytes(bytes);
    String token = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    service
        .jdbc()
        .update(
            "INSERT INTO api_tokens(digest,member_id,scope,expires_at) VALUES (?,?,?,?)",
            Json.hashBytes(token.getBytes(StandardCharsets.UTF_8)),
            m.get("id"),
            body.scope(),
            Json.now() + body.days() * 86400000L);
    return Map.of("token", token, "scope", body.scope());
  }
}
