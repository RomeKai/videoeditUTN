/**
 * Test data of this feature. What the screen tests share (the providers, the
 * mocked API, `renderScenario`) is in `@/testing`.
 */

/** Ids of the default scenario: the clips of «Vlog Mendoza» (project 5), all rendered, with SEO. */
export const CLIPS = {
  first: '00000008-0000-4000-8000-0000000001f5',
  second: '00000008-0000-4000-8000-0000000001f6',
  third: '00000008-0000-4000-8000-0000000001f7',
  /** Its only publication failed (TikTok): on a plan without crossposting it can still go out on one network. */
  fourth: '00000008-0000-4000-8000-0000000001f8',
} as const;
