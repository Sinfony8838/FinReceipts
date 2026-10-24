package io.finreceipts;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.csrf.*;
import org.springframework.session.web.http.DefaultCookieSerializer;
import org.springframework.web.filter.OncePerRequestFilter;

@Configuration
class Security {
  @Bean
  PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder();
  }

  @Bean
  UserDetailsService users(JdbcTemplate db) {
    return name -> {
      var rows = db.queryForList("SELECT * FROM members WHERE username=? AND enabled=true", name);
      if (rows.isEmpty()) throw new UsernameNotFoundException("unknown_user");
      var m = rows.getFirst();
      return User.withUsername(name)
          .password(m.get("password_hash").toString())
          .roles(m.get("role").toString())
          .build();
    };
  }

  @Bean
  DefaultCookieSerializer cookie(@Value("${finreceipts.secure-cookie}") boolean secure) {
    var c = new DefaultCookieSerializer();
    c.setCookieName("FRSESSION");
    c.setUseSecureCookie(secure);
    c.setSameSite("Lax");
    return c;
  }

  @Bean
  SecurityFilterChain chain(
      HttpSecurity http,
      JdbcTemplate db,
      UserDetailsService users,
      @Value("${finreceipts.worker-secret}") String secret)
      throws Exception {
    if (secret.length() < 32)
      throw new IllegalArgumentException("WORKER_SECRET must have at least 32 characters");
    var tokenFilter =
        new OncePerRequestFilter() {
          @Override
          protected void doFilterInternal(
              HttpServletRequest req, HttpServletResponse res, FilterChain chain)
              throws ServletException, IOException {
            String auth = req.getHeader("Authorization");
            if (auth != null && auth.startsWith("Bearer ")) {
              String token = auth.substring(7);
              UserDetails user = null;
              if (req.getRequestURI().startsWith("/internal/v1/")) {
                if (MessageDigest.isEqual(
                    token.getBytes(StandardCharsets.UTF_8),
                    secret.getBytes(StandardCharsets.UTF_8)))
                  user = User.withUsername("worker").password("").roles("WORKER").build();
              } else {
                var rows =
                    db.queryForList(
                        "SELECT m.username,t.scope FROM api_tokens t JOIN members m ON m.id=t.member_id WHERE t.digest=? AND t.revoked=false AND t.expires_at>? AND m.enabled=true",
                        Json.hashBytes(token.getBytes(StandardCharsets.UTF_8)),
                        Json.now());
                if (!rows.isEmpty()) {
                  var row = rows.getFirst();
                  user = users.loadUserByUsername(row.get("username").toString());
                  req.setAttribute("tokenScope", row.get("scope"));
                }
              }
              if (user == null) {
                res.setStatus(401);
                res.setContentType("application/json");
                res.getWriter().write("{\"code\":\"invalid_token\"}");
                return;
              }
              SecurityContextHolder.getContext()
                  .setAuthentication(
                      new UsernamePasswordAuthenticationToken(user, null, user.getAuthorities()));
              req.setAttribute("bearerAuthenticated", true);
            }
            chain.doFilter(req, res);
          }
        };
    http.addFilterBefore(tokenFilter, CsrfFilter.class)
        .csrf(
            c ->
                c.csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse())
                    .csrfTokenRequestHandler(new CsrfTokenRequestAttributeHandler())
                    .ignoringRequestMatchers(
                        r -> Boolean.TRUE.equals(r.getAttribute("bearerAuthenticated"))))
        .authorizeHttpRequests(
            a ->
                a.requestMatchers("/actuator/health", "/api/v2/csrf", "/api/v2/login", "/error")
                    .permitAll()
                    .requestMatchers("/internal/v1/**")
                    .hasRole("WORKER")
                    .requestMatchers("/actuator/prometheus")
                    .permitAll()
                    .requestMatchers("/api/v2/**", "/v3/api-docs/**", "/swagger-ui/**")
                    .hasAnyRole("ADMIN", "ANALYST", "REVIEWER")
                    .anyRequest()
                    .denyAll())
        .formLogin(
            f ->
                f.loginProcessingUrl("/api/v2/login")
                    .successHandler(
                        (q, s, a) -> {
                          s.setContentType("application/json");
                          s.getWriter().write("{\"authenticated\":true}");
                        })
                    .failureHandler(
                        (q, s, e) -> {
                          s.setStatus(401);
                          s.setContentType("application/json");
                          s.getWriter().write("{\"code\":\"login_failed\"}");
                        }))
        .logout(
            l -> l.logoutUrl("/api/v2/logout").logoutSuccessHandler((q, s, a) -> s.setStatus(204)))
        .exceptionHandling(
            e ->
                e.authenticationEntryPoint(
                    (q, s, x) -> {
                      s.setStatus(401);
                      s.setContentType("application/json");
                      s.getWriter().write("{\"code\":\"authentication_required\"}");
                    }));
    return http.build();
  }
}
