#include "serial_line_reader.h"

SerialLineReader::SerialLineReader(char* buffer, size_t bufferSize)
    : buffer_(buffer),
      bufferSize_(bufferSize),
      length_(0),
      trailingCr_(false),
      discarding_(false) {
  if (bufferSize_ > 0) {
    buffer_[0] = '\0';
  }
}

SerialLineReadResult SerialLineReader::push(char incoming) {
  if (discarding_) {
    if (incoming == '\n') {
      reset();
    }
    return SerialLineReadResult::NONE;
  }

  if (trailingCr_) {
    if (incoming == '\n') {
      buffer_[length_] = '\0';
      length_ = 0;
      trailingCr_ = false;
      return SerialLineReadResult::COMPLETE;
    }

    length_ = 0;
    trailingCr_ = false;
    discarding_ = true;
    return SerialLineReadResult::REJECTED;
  }

  if (incoming == '\r') {
    if (length_ == 0) {
      discarding_ = true;
      return SerialLineReadResult::REJECTED;
    }
    trailingCr_ = true;
    return SerialLineReadResult::NONE;
  }

  if (incoming == '\n') {
    if (length_ == 0) {
      return SerialLineReadResult::NONE;
    }
    buffer_[length_] = '\0';
    length_ = 0;
    return SerialLineReadResult::COMPLETE;
  }

  if (bufferSize_ > 0 && length_ < bufferSize_ - 1) {
    buffer_[length_++] = incoming;
    return SerialLineReadResult::NONE;
  }

  length_ = 0;
  discarding_ = true;
  return SerialLineReadResult::OVERFLOW;
}

const char* SerialLineReader::line() const {
  return buffer_;
}

void SerialLineReader::reset() {
  length_ = 0;
  trailingCr_ = false;
  discarding_ = false;
  if (bufferSize_ > 0) {
    buffer_[0] = '\0';
  }
}
