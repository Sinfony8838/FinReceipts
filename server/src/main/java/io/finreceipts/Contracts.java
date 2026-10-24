package io.finreceipts;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.*;

final class Contracts {
  record Target(
      @NotNull @Pattern(regexp = "direct|ratio|yoy_growth") String kind,
      @NotBlank @Size(max = 64) String metric,
      @Min(1990) @Max(2100) int fiscal_year,
      @Size(max = 64) String denominator) {}

  record Item(
      @NotNull @Pattern(regexp = "metric|research") String kind,
      @NotNull @Pattern(regexp = "[A-Z0-9.\\-]{1,16}") String ticker,
      @NotNull @Valid Target target,
      @Size(max = 12000) String question) {}

  record Batch(
      @NotBlank @Size(max = 120) String title,
      @Size(min = 1, max = 100) @NotNull List<@NotNull @Valid Item> items) {}

  record Revision(@Min(1) long expected_revision, boolean acknowledge_possible_duplicate) {}

  record Review(
      @Min(1) long expected_revision,
      @NotNull @Pattern(regexp = "ACCEPTED|REJECTED") String decision,
      @NotBlank @Size(max = 2000) String reason) {}

  record Claim(@Min(1) long expected_revision, @NotBlank @Size(max = 100) String worker_id) {}

  record Lease(@NotBlank String lease_token) {}

  record Reservation(
      @NotBlank String lease_token,
      @NotBlank String attempt_id,
      @Min(1) @Max(1000000) long reserve_micros) {}

  record Settlement(
      @NotBlank String lease_token,
      @NotBlank String attempt_id,
      @Min(0) long actual_micros,
      @NotNull Map<String, Object> usage) {}

  record Commit(
      @NotBlank String lease_token,
      @Min(1) long expected_revision,
      @NotNull @Pattern(regexp = "MODEL|VERIFY|DONE") String next_step,
      @NotNull Map<String, Object> checkpoint,
      Map<String, Object> evidence,
      Map<String, Object> result,
      @Pattern(regexp = "SUPPORTED|CONTRADICTED|UNSUPPORTED|PENDING") String machine_verdict) {}

  record Failure(
      @NotBlank String lease_token,
      @NotNull @Pattern(regexp = "[a-z0-9_]{1,100}") String code,
      boolean unknown) {}

  record Member(
      @NotNull @Pattern(regexp = "[a-zA-Z0-9_.-]{3,80}") String username,
      @NotNull @Size(min = 12, max = 100) String password,
      @NotNull @Pattern(regexp = "ADMIN|ANALYST|REVIEWER") String role) {}

  record Token(@NotNull @Pattern(regexp = "read|submit") String scope, @Min(1) @Max(90) int days) {}
}
