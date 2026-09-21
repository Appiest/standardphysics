// Compile against the official nianticlabs/spz v2.0.0 library and system zlib.
// Preserve the raw scene axes; the viewer applies the room transform explicitly.
#include "load-spz.h"
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <iostream>

int main(int argc, char **argv) {
  if (argc != 3 || std::filesystem::exists(argv[2])) {
    std::cerr << "Usage: compress_moffett_splat INPUT.ply NEW_OUTPUT.spz\n";
    return 1;
  }
  const auto source = spz::loadSplatFromPly(argv[1], {});
  if (source.numPoints <= 0) return 2;
  for (auto x : source.positions) {
    if (!std::isfinite(x) || std::abs(x) >= 2047.0f) {
      std::cerr << "Position outside SPZ fixed-point range\n";
      return 3;
    }
  }
  if (!spz::saveSpz(source, {}, argv[2])) return 4;
  const auto decoded = spz::loadSpz(argv[2], {});
  if (decoded.numPoints != source.numPoints || decoded.shDegree != source.shDegree ||
      decoded.positions.size() != source.positions.size()) return 5;
  float error = 0;
  for (size_t i = 0; i < source.positions.size(); ++i) {
    error = std::max(error, std::abs(source.positions[i] - decoded.positions[i]));
  }
  if (error > 0.00025f) return 6;
  std::cout << "{\"splats\":" << source.numPoints << ",\"sh_degree\":" << source.shDegree
            << ",\"maximum_coordinate_error_m\":" << error
            << ",\"output_bytes\":" << std::filesystem::file_size(argv[2]) << "}\n";
}
