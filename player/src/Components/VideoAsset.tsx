import { FC } from "react";
import { videoSchema } from "@videofy/types";
import { z } from "zod";
import { getRemotionEnvironment, Loop, OffthreadVideo, Video } from "remotion";
import { roundToNearestFrame } from "../utils/timestamps";

interface Props {
  asset: z.infer<typeof videoSchema>;
  volume: number;
}

const style = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
  objectPosition: "center",
  transformOrigin: "center",
} as const;

const VideoAsset: FC<Props> = ({ asset, volume = 0 }) => {
  const startFrom = asset?.startFrom && roundToNearestFrame(asset.startFrom);
  const endAt = asset?.endAt && roundToNearestFrame(asset.endAt);
  // While rendering, <Video> seeks the browser's own player and can show an earlier frame
  // (the picture jumps back and forth); OffthreadVideo takes each frame exactly from the file.
  // It has no `loop`, so <Loop> repeats the trimmed clip instead.
  if (getRemotionEnvironment().isRendering) {
    const video = <OffthreadVideo src={asset.url} startFrom={startFrom} endAt={endAt} style={style} volume={volume} />;
    const length = endAt ? endAt - (startFrom || 0) : 0;
    return length > 0 ? <Loop durationInFrames={length}>{video}</Loop> : video;
  }
  return (
    <Video
      src={asset.url}
      startFrom={startFrom}
      endAt={endAt}
      loop
      style={style}
      volume={volume}
      pauseWhenBuffering={true}
    />
  );
};

export default VideoAsset;
