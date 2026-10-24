package io.finreceipts;

import java.util.Map;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.*;

class Problem extends RuntimeException {
  final int status;

  Problem(int status, String code) {
    super(code);
    this.status = status;
  }

  static Problem conflict(String code) {
    return new Problem(409, code);
  }
}

@RestControllerAdvice
class ProblemHandler {
  @ExceptionHandler(Problem.class)
  ResponseEntity<?> handle(Problem e) {
    return ResponseEntity.status(e.status).body(Map.of("code", e.getMessage()));
  }

  @ExceptionHandler({
    MethodArgumentNotValidException.class,
    HttpMessageNotReadableException.class,
    IllegalArgumentException.class
  })
  ResponseEntity<?> invalid(Exception e) {
    return ResponseEntity.unprocessableEntity().body(Map.of("code", "invalid_request"));
  }

  @ExceptionHandler(DataAccessException.class)
  ResponseEntity<?> busy(DataAccessException e) {
    return ResponseEntity.status(503)
        .header("Retry-After", "1")
        .body(Map.of("code", "store_unavailable"));
  }
}
