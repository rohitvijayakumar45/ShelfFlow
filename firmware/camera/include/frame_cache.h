#pragma once

#include <esp_camera.h>
#include <atomic>
#include <memory>
#include <mutex>

struct CachedFrame {
  uint8_t* data = nullptr;
  size_t size = 0;
  uint16_t width = 0, height = 0;
  uint32_t sequence = 0;
  uint64_t captureUs = 0;
  ~CachedFrame();
};

// The capture task owns camera reads. Network clients retain an immutable JPEG,
// so slow sends do not hold a camera framebuffer or block newer captures.
class FrameCache {
 public:
  bool begin();
  std::shared_ptr<const CachedFrame> latest();
  bool setResolution(framesize_t resolution);
  bool tune(int clockMHz, int quality, int fps, int resolution = -1);
  std::atomic<int> clockMHz{8}, quality{12}, fps{20};

 private:
  static void task(void* context);
  void capture();
  std::mutex sensorMutex, cacheMutex;
  std::shared_ptr<const CachedFrame> frame;
  uint32_t nextSequence = 0;
};
