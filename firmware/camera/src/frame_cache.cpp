#include "frame_cache.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <cstring>

CachedFrame::~CachedFrame() { heap_caps_free(data); }

bool FrameCache::begin() {
  return xTaskCreatePinnedToCore(task, "capture", 4096, this, 1, nullptr, 1) == pdPASS;
}

std::shared_ptr<const CachedFrame> FrameCache::latest() {
  std::lock_guard<std::mutex> guard(cacheMutex);
  // A retained image must never make a stalled camera look live.
  if (!frame || esp_timer_get_time() - frame->captureUs > 2000000) return nullptr;
  return frame;
}

bool FrameCache::setResolution(framesize_t resolution) {
  std::lock_guard<std::mutex> sensorGuard(sensorMutex);
  auto* sensor = esp_camera_sensor_get();
  if (!sensor || sensor->set_framesize(sensor, resolution) != 0) return false;
  std::lock_guard<std::mutex> cacheGuard(cacheMutex);
  frame.reset();
  return true;
}

bool FrameCache::tune(int clock, int jpegQuality, int captureFps, int requestedResolution) {
  if ((clock != 8 && clock != 16 && clock != 20) || jpegQuality < 10 || jpegQuality > 30 ||
      captureFps < 5 || captureFps > 25) return false;
  if (requestedResolution != -1 && requestedResolution != FRAMESIZE_QVGA &&
      requestedResolution != FRAMESIZE_HVGA && requestedResolution != FRAMESIZE_VGA &&
      requestedResolution != FRAMESIZE_SVGA) return false;
  std::lock_guard<std::mutex> sensorGuard(sensorMutex);
  auto* sensor = esp_camera_sensor_get();
  if (!sensor) return false;
  const auto resolution = sensor->status.framesize;
  const auto nextResolution = requestedResolution == -1 ? resolution : static_cast<framesize_t>(requestedResolution);
  const bool ok = sensor->set_xclk(sensor, LEDC_TIMER_0, clock) == 0 &&
      sensor->set_framesize(sensor, nextResolution) == 0 &&
      sensor->set_quality(sensor, jpegQuality) == 0;
  if (ok) {
    clockMHz = clock;
    quality = jpegQuality;
    fps = captureFps;
  } else {
    sensor->set_xclk(sensor, LEDC_TIMER_0, clockMHz.load());
    sensor->set_framesize(sensor, resolution);
    sensor->set_quality(sensor, quality.load());
  }
  std::lock_guard<std::mutex> cacheGuard(cacheMutex);
  frame.reset();
  return ok;
}

void FrameCache::capture() {
  std::lock_guard<std::mutex> sensorGuard(sensorMutex);
  auto* fb = esp_camera_fb_get();
  if (!fb) return;
  if (fb->format != PIXFORMAT_JPEG || fb->len < 4 || fb->buf[0] != 0xff || fb->buf[1] != 0xd8) {
    esp_camera_fb_return(fb);
    return;
  }
  auto next = std::make_shared<CachedFrame>();
  next->data = static_cast<uint8_t*>(heap_caps_malloc(fb->len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!next->data) next->data = static_cast<uint8_t*>(heap_caps_malloc(fb->len, MALLOC_CAP_8BIT));
  if (next->data) {
    memcpy(next->data, fb->buf, fb->len);
    next->size = fb->len;
    next->width = fb->width;
    next->height = fb->height;
    next->captureUs = uint64_t(fb->timestamp.tv_sec) * 1000000 + fb->timestamp.tv_usec;
    next->sequence = nextSequence++;
  }
  esp_camera_fb_return(fb);
  if (next->data) {
    std::lock_guard<std::mutex> cacheGuard(cacheMutex);
    frame = next;
  }
}

void FrameCache::task(void* context) {
  auto* self = static_cast<FrameCache*>(context);
  for (;;) {
    const TickType_t started = xTaskGetTickCount();
    self->capture();
    const TickType_t elapsed = xTaskGetTickCount() - started;
    const TickType_t period = pdMS_TO_TICKS(1000 / self->fps.load());
    vTaskDelay(elapsed < period ? period - elapsed : 1);
  }
}
