package io.finreceipts;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

final class Json {
  static final ObjectMapper MAPPER =
      new ObjectMapper().configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);

  static String write(Object value) {
    try {
      return MAPPER.writeValueAsString(value);
    } catch (Exception e) {
      throw new IllegalArgumentException("invalid_json");
    }
  }

  static Map<String, Object> map(Object value) {
    try {
      return MAPPER.readValue(value.toString(), new TypeReference<>() {});
    } catch (Exception e) {
      throw new IllegalArgumentException("invalid_json");
    }
  }

  static String hash(Object value) {
    return hashBytes(write(value).getBytes(StandardCharsets.UTF_8));
  }

  static String hashBytes(byte[] value) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
    } catch (Exception e) {
      throw new IllegalStateException(e);
    }
  }

  static String id() {
    return UUID.randomUUID().toString();
  }

  static long now() {
    return System.currentTimeMillis();
  }

  static long number(Map<String, Object> m, String key) {
    return ((Number) m.get(key)).longValue();
  }
}
