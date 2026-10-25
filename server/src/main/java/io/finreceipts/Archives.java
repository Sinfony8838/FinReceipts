package io.finreceipts;

import jakarta.servlet.http.HttpServletRequest;
import java.security.Principal;
import java.util.*;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

/** Original exports are retained byte-for-byte and never become executable tasks. */
@RestController
@RequestMapping("/api/v2/archives")
class Archives {
  final Api api;

  Archives(Api api) {
    this.api = api;
  }

  @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
  ResponseEntity<?> create(@RequestBody byte[] bytes, Principal p, HttpServletRequest request) {
    var m = api.identity(p, request, true);
    PlatformService.role(m, "ADMIN", "ANALYST");
    if (bytes.length > 2000000) throw new Problem(413, "archive_too_large");
    var document = Json.map(new String(bytes, java.nio.charset.StandardCharsets.UTF_8));
    if (!(document.get("run") instanceof Map) || !(document.get("events") instanceof List))
      throw new IllegalArgumentException("legacy_export_required");
    String digest = Json.hashBytes(bytes), tenant = m.get("tenant_id").toString();
    api.service
        .jdbc()
        .update(
            "INSERT IGNORE INTO archives(id,tenant_id,creator_id,document,created_at) VALUES (?,?,?,?,?)",
            digest,
            tenant,
            m.get("id"),
            bytes,
            Json.now());
    return ResponseEntity.status(201)
        .body(
            Map.of(
                "id",
                digest,
                "sha256",
                digest,
                "read_only",
                true,
                "verification",
                "Byte preservation only; use the legacy verifier to validate the event chain."));
  }

  @GetMapping
  Map<String, Object> list(
      Principal p, HttpServletRequest request, @RequestParam(defaultValue = "") String before) {
    String tenant = api.identity(p, request, false).get("tenant_id").toString();
    return Api.page(
        api.service
            .jdbc()
            .queryForList(
                "SELECT id,creator_id,created_at FROM archives WHERE tenant_id=? AND (?='' OR id<?) ORDER BY id DESC LIMIT 30",
                tenant,
                before,
                before),
        30);
  }

  @GetMapping("/{id}")
  ResponseEntity<byte[]> read(@PathVariable String id, Principal p, HttpServletRequest request) {
    String tenant = api.identity(p, request, false).get("tenant_id").toString();
    var rows =
        api.service
            .jdbc()
            .queryForList("SELECT document FROM archives WHERE id=? AND tenant_id=?", id, tenant);
    if (rows.isEmpty()) throw new Problem(404, "archive_not_found");
    return ResponseEntity.ok()
        .contentType(MediaType.APPLICATION_JSON)
        .header("Content-Disposition", "attachment; filename=legacy-export.json")
        .body((byte[]) rows.getFirst().get("document"));
  }
}
