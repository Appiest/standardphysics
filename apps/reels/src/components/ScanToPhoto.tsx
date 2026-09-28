import { LidarRoom, type OrbitCamera } from "./LidarRoom";
import { ScanBar } from "./ScanBar";
import { TexturedRoom } from "./TexturedRoom";

type ScanToPhotoProps = {
  scan: string;
  width: number;
  height: number;
  camera: OrbitCamera;
  /** How much of the measured room has appeared, 0 to 1, as the LiDAR sweep crosses it. */
  measured: number;
  /** How far down the frame the photo pass has painted, 0 (all clay) to 1 (all photographs). */
  painted: number;
  cutaway?: number;
  radius?: number;
};

/**
 * The two passes the app makes over a scan, shown one after the other: the LiDAR measures the room as bare clay,
 * then the scan bar comes back down and paints it with the photos the phone took on the walk.
 */
export function ScanToPhoto({ scan, width, height, camera, measured, painted, cutaway = 2.3, radius = 40 }: ScanToPhotoProps) {
  const sweeping = painted > 0 && painted < 1;
  return (
    <div className="absolute inset-0" style={{ width, height }}>
      {painted < 1 && <LidarRoom scan={scan} width={width} height={height} camera={camera} reveal={measured} cutaway={cutaway} radius={radius} />}
      {painted > 0 && (
        <div className="absolute inset-0" style={{ clipPath: `inset(0 0 ${(1 - painted) * 100}% 0)` }}>
          <TexturedRoom scan={scan} width={width} height={height} camera={camera} cutaway={cutaway} />
        </div>
      )}
      {sweeping && <ScanBar at={painted} trail={0.08} />}
    </div>
  );
}
