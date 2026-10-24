package io.finreceipts;

import jakarta.validation.Valid;
import java.util.Map;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/internal/v1")
class WorkerApi {
  final PlatformService service;
  final RateControl rate;

  WorkerApi(PlatformService service, RateControl rate) {
    this.service = service;
    this.rate = rate;
  }

  @PostMapping("/tasks/{id}/claim")
  Map<String, Object> claim(@PathVariable String id, @Valid @RequestBody Contracts.Claim body) {
    return service.claim(id, body);
  }

  @PostMapping("/tasks/{id}/heartbeat")
  Map<String, Boolean> heartbeat(
      @PathVariable String id, @Valid @RequestBody Contracts.Lease body) {
    service.heartbeat(id, body.lease_token());
    return Map.of("ok", true);
  }

  @PostMapping("/tasks/{id}/reserve")
  Map<String, Object> reserve(
      @PathVariable String id, @Valid @RequestBody Contracts.Reservation body) {
    rate.acquire("model-starts", 16);
    return service.reserve(id, body);
  }

  @PostMapping("/tasks/{id}/settle")
  Map<String, Boolean> settle(
      @PathVariable String id, @Valid @RequestBody Contracts.Settlement body) {
    service.settle(id, body);
    return Map.of("ok", true);
  }

  @PostMapping("/tasks/{id}/commit")
  Map<String, Object> commit(@PathVariable String id, @Valid @RequestBody Contracts.Commit body) {
    return service.commit(id, body);
  }

  @PostMapping("/tasks/{id}/fail")
  Map<String, Boolean> fail(@PathVariable String id, @Valid @RequestBody Contracts.Failure body) {
    service.fail(id, body);
    return Map.of("ok", true);
  }

  @PostMapping("/source/permit")
  Map<String, Boolean> source() {
    rate.acquire("sec", 8);
    return Map.of("ok", true);
  }

  @PostMapping("/source/{key}/lock")
  Map<String, Boolean> lock(@PathVariable String key, @Valid @RequestBody Contracts.Lease body) {
    if (!key.matches("[a-f0-9]{64}")) throw new IllegalArgumentException("invalid_source_key");
    return Map.of("acquired", rate.sourceLock(key, body.lease_token()));
  }

  @PostMapping("/source/{key}/unlock")
  Map<String, Boolean> unlock(@PathVariable String key, @Valid @RequestBody Contracts.Lease body) {
    rate.releaseSource(key, body.lease_token());
    return Map.of("ok", true);
  }
}
