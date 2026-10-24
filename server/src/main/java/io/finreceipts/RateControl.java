package io.finreceipts;

import java.time.Duration;
import java.util.List;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
class RateControl {
  final StringRedisTemplate redis;
  static final DefaultRedisScript<Long> COUNT =
      new DefaultRedisScript<>(
          "local n=redis.call('INCR',KEYS[1]);if n==1 then redis.call('PEXPIRE',KEYS[1],ARGV[1]) end;return n",
          Long.class);

  RateControl(StringRedisTemplate redis) {
    this.redis = redis;
  }

  void health() {
    try {
      redis.hasKey("fr:v2:dispatch-health");
    } catch (Exception e) {
      throw new Problem(503, "rate_service_unavailable");
    }
  }

  void acquire(String key, int maximum) {
    try {
      Long n =
          redis.execute(COUNT, List.of("fr:v2:rate:" + key + ":" + (Json.now() / 1000)), "2000");
      if (n == null) throw new IllegalStateException();
      if (n > maximum) throw new Problem(429, "rate_limited");
    } catch (Problem e) {
      throw e;
    } catch (Exception e) {
      throw new Problem(503, "rate_service_unavailable");
    }
  }

  boolean sourceLock(String key, String token) {
    try {
      return Boolean.TRUE.equals(
          redis.opsForValue().setIfAbsent("fr:v2:source:" + key, token, Duration.ofSeconds(40)));
    } catch (Exception e) {
      throw new Problem(503, "source_lock_unavailable");
    }
  }

  void releaseSource(String key, String token) {
    redis.execute(
        new DefaultRedisScript<>(
            "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end;return 0",
            Long.class),
        List.of("fr:v2:source:" + key),
        token);
  }

  String openStream(String tenant) {
    String token = Json.id();
    var script =
        new DefaultRedisScript<>(
            "redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',ARGV[1]);if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[3]) then return 0 end;redis.call('ZADD',KEYS[1],ARGV[2],ARGV[4]);redis.call('EXPIRE',KEYS[1],60);return 1",
            Long.class);
    try {
      Long acquired =
          redis.execute(
              script,
              List.of("fr:v2:sse:" + tenant),
              Long.toString(Json.now()),
              Long.toString(Json.now() + 40000),
              "600",
              token);
      if (!Long.valueOf(1).equals(acquired)) throw new Problem(429, "sse_capacity");
      return token;
    } catch (Problem e) {
      throw e;
    } catch (Exception e) {
      throw new Problem(503, "stream_capacity_unavailable");
    }
  }

  void closeStream(String tenant, String token) {
    try {
      redis.opsForZSet().remove("fr:v2:sse:" + tenant, token);
    } catch (Exception ignored) {
      /* Expiry reclaims a crashed API's permits. */
    }
  }
}
