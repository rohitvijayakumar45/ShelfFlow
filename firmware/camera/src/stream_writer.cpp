#include "stream_writer.h"
#include <esp_timer.h>
#include <lwip/sockets.h>
#include <errno.h>

bool writeBefore(WiFiClient& client, const uint8_t* data, size_t size, int64_t deadlineUs) {
  while (size && esp_timer_get_time() < deadlineUs) {
    const int fd = client.fd();
    if (fd < 0) return false;
    const int sent = send(fd, data, size, MSG_DONTWAIT);
    if (sent > 0) {
      data += sent;
      size -= sent;
    } else if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
      vTaskDelay(1);
    } else {
      return false;
    }
  }
  return size == 0;
}
