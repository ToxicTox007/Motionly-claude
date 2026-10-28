// The animation features LazyMotion loads after first paint (see
// motion-provider.tsx). Kept in its own module so the bundler splits it out.
import { domAnimation } from "motion/react";

export default domAnimation;
