package io.finreceipts;

import io.micrometer.core.instrument.MeterRegistry;
import java.nio.file.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** SQL remains authoritative; failed volume copies are visible and retried. */
@Component
class ArtifactCopies {
  final PlatformService service;
  final Path root;
  final MeterRegistry metrics;

  ArtifactCopies(
      PlatformService service,
      @Value("${finreceipts.artifacts}") String root,
      MeterRegistry metrics) {
    this.service = service;
    this.root = Path.of(root).toAbsolutePath().normalize();
    this.metrics = metrics;
  }

  @Scheduled(fixedDelay = 5000)
  void copy() {
    try {
      Files.createDirectories(root);
      for (var row :
          service
              .jdbc()
              .queryForList(
                  "SELECT digest,document FROM artifact_copies WHERE copied=false ORDER BY created_at LIMIT 16")) {
        String digest = row.get("digest").toString();
        if (!digest.matches("[a-f0-9]{64}"))
          throw new IllegalStateException("invalid_artifact_digest");
        byte[] data = (byte[]) row.get("document");
        Path target = root.resolve(digest + ".json");
        if (Files.exists(target)) {
          if (!digest.equals(Json.hashBytes(Files.readAllBytes(target))))
            throw new IllegalStateException("artifact_digest_mismatch");
        } else {
          Path temporary = Files.createTempFile(root, "pending-", ".json");
          try {
            Files.write(temporary, data);
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
          } finally {
            Files.deleteIfExists(temporary);
          }
        }
        service.jdbc().update("UPDATE artifact_copies SET copied=true WHERE digest=?", digest);
      }
    } catch (Exception e) {
      metrics.counter("finreceipts_artifact_copy_failures").increment();
    }
  }
}
