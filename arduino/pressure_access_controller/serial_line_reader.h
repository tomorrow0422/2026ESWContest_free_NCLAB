#pragma once

#include <stddef.h>

enum class SerialLineReadResult {
  NONE,
  COMPLETE,
  REJECTED,
  OVERFLOW,
};

class SerialLineReader {
 public:
  SerialLineReader(char* buffer, size_t bufferSize);

  SerialLineReadResult push(char incoming);
  const char* line() const;

 private:
  void reset();

  char* buffer_;
  size_t bufferSize_;
  size_t length_;
  bool trailingCr_;
  bool discarding_;
};
