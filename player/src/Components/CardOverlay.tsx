import { CSSProperties, FC } from "react";
import { AbsoluteFill, Easing, Img, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

const REVEAL_FRAMES = 16;
const STEP_FRAMES = 8;
const fill: CSSProperties = { width: "100%", height: "100%", objectFit: "cover" };

interface Props {
  overlay: { url: string; base?: string; reveal?: boolean; steps?: { url: string; at: number }[] };
}

/**
 * The card above the photo: an optional still layer, then the text, which can fade in top to bottom,
 * then the layers that come in later, each at its own second (a detail line when the voice reaches it).
 */
export const CardOverlay: FC<Props> = ({ overlay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = overlay.reveal
    ? interpolate(frame, [0, REVEAL_FRAMES], [0, 1], {
        easing: Easing.out(Easing.cubic),
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 1;
  // The soft edge of the mask travels past the bottom of the frame; a finished reveal needs no mask.
  const edge = progress * 120;
  const mask = `linear-gradient(to bottom, #000 ${edge - 20}%, transparent ${edge}%)`;
  const revealing: CSSProperties =
    progress < 1
      ? {
          maskImage: mask,
          WebkitMaskImage: mask,
          transform: `translateY(${(1 - progress) * 24}px)`,
        }
      : {};

  return (
    <>
      {overlay.base && (
        <AbsoluteFill>
          <Img src={overlay.base} style={fill} />
        </AbsoluteFill>
      )}
      <AbsoluteFill style={revealing}>
        <Img src={overlay.url} style={fill} />
      </AbsoluteFill>
      {(overlay.steps ?? []).map((step) => {
        const shown = interpolate(frame, [step.at * fps, step.at * fps + STEP_FRAMES], [0, 1], {
          easing: Easing.out(Easing.cubic),
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        return (
          <AbsoluteFill key={step.url} style={{ opacity: shown, transform: `translateX(${(1 - shown) * -28}px)` }}>
            <Img src={step.url} style={fill} />
          </AbsoluteFill>
        );
      })}
    </>
  );
};
