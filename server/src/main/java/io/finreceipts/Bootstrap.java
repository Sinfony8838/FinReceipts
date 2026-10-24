package io.finreceipts;

import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
class Bootstrap implements ApplicationRunner {
  private final JdbcTemplate db;
  private final PasswordEncoder encoder;
  private final String password;
  private final long budget;

  Bootstrap(
      JdbcTemplate db,
      PasswordEncoder encoder,
      @Value("${finreceipts.bootstrap-password}") String password,
      @Value("${finreceipts.global-budget-micros}") long budget) {
    this.db = db;
    this.encoder = encoder;
    this.password = password;
    this.budget = budget;
  }

  public void run(ApplicationArguments args) {
    if (password.length() < 16)
      throw new IllegalArgumentException("BOOTSTRAP_PASSWORD must have at least 16 characters");
    if (budget < 0 || budget > 20000000)
      throw new IllegalArgumentException("authorized global ceiling is CNY 20");
    db.update(
        "UPDATE provider_budget SET limit_micros=LEAST(limit_micros,?) WHERE id='global'", budget);
    for (String tenant : List.of("alpha", "beta")) {
      db.update("INSERT IGNORE INTO tenants(id,name) VALUES (?,?)", tenant, tenant + " research");
      for (String role : List.of("ADMIN", "ANALYST", "REVIEWER")) {
        String username = tenant + "." + role.toLowerCase(Locale.ROOT);
        db.update(
            "INSERT IGNORE INTO members(id,tenant_id,username,password_hash,role) VALUES (?,?,?,?,?)",
            Json.id(),
            tenant,
            username,
            encoder.encode(password),
            role);
      }
    }
  }
}
