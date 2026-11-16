import { FC, ReactNode } from "react";
import { AbsoluteFill, Img, interpolate, Sequence, useCurrentFrame } from "remotion";
import type { z } from "zod";
import { cameraMovementsEnum, imageSchema, videoSchema } from "@videofy/types";
import { ImageAnimation } from "./ImageAnimation";
import VideoAsset from "./VideoAsset";

type Shot = z.infer<typeof imageSchema> | z.infer<typeof videoSchema>;
type Movement = z.infer<typeof cameraMovementsEnum>;

// A story with several photos shows them in turn, each fading in over the one before.
const FADE = 12;

function turns(count: number, durationInFrames: number) {
  const slice = Math.floor(durationInFrames / count);
  return Array.from({ length: count }, (_, k) => {
    const start = k * slice;
    const end = k === count - 1 ? durationInFrames : (k + 1) * slice;
    return { start, end };
  });
}

const FadeIn: FC<{ fade: number; children: ReactNode }> = ({ fade, children }) => {
  const frame = useCurrentFrame();
  const opacity = fade ? interpolate(frame, [0, fade], [0, 1], { extrapolateRight: "clamp" }) : 1;
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

/** The photos (and clips) under the card, one after another. */
export const Slideshow: FC<{ shots: Shot[]; durationInFrames: number; movement: Movement }> = ({
  shots,
  durationInFrames,
  movement,
}) => {
  const other: Movement = movement === "zoom-out" ? "zoom-in" : "zoom-out";
  return (
    <>
      {turns(shots.length, durationInFrames).map(({ start, end }, k) => {
        const from = Math.max(0, start - (k ? FADE : 0));
        const length = end - from;
        const shot = shots[k];
        return (
          <Sequence key={`shot-${k}`} from={from} durationInFrames={length}>
            <FadeIn fade={k ? FADE : 0}>
              {shot.type === "video" ? (
                <VideoAsset asset={shot} volume={0} />
              ) : (
                <ImageAnimation asset={shot} cameraMovement={k % 2 ? other : movement} durationInFrames={length} />
              )}
            </FadeIn>
          </Sequence>
        );
      })}
    </>
  );
};

/** Each photo's tag ('Ảnh minh họa · Author, CC BY 4.0'), above the card, while that photo shows. */
export const SlideLabels: FC<{ shots: Shot[]; durationInFrames: number }> = ({ shots, durationInFrames }) => (
  <>
    {turns(shots.length, durationInFrames).map(({ start, end }, k) =>
      shots[k].label ? (
        <Sequence key={`label-${k}`} from={start} durationInFrames={end - start}>
          <FadeIn fade={k ? 8 : 0}>
            <Img src={shots[k].label as string} style={{ width: "100%", height: "100%" }} />
          </FadeIn>
        </Sequence>
      ) : null
    )}
  </>
);
