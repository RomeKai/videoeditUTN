export interface Reasoning {
  /** Two or three short labels taken from the text. Empty when it has no short parts. */
  chips: string[];
  /** The full sentence. */
  sentence: string;
}

const capitalize = (text: string) => (text ? `${text[0]?.toUpperCase() ?? ''}${text.slice(1)}` : text);

/**
 * "Por qué" of a suggested clip (map 9.1.5): 2 or 3 short labels taken from
 * `ai_reasoning` plus the whole sentence. Nothing is invented: the labels are
 * pieces of the text, either the ones the model already separated with "·"
 * or the short clauses of its first sentence.
 */
export function explainReasoning(text: string | null | undefined): Reasoning {
  const clean = (text ?? '').replace(/\s+/g, ' ').trim();
  if (!clean) return { chips: [], sentence: '' };
  if (clean.includes(' · ')) {
    const lastDot = clean.lastIndexOf(' · ');
    const stop = clean.indexOf('. ', lastDot);
    const head = stop === -1 ? clean : clean.slice(0, stop);
    const rest = stop === -1 ? '' : clean.slice(stop + 2).trim();
    const chips = head
      .split(' · ')
      .map((part) => part.trim().replace(/\.$/, ''))
      .filter(Boolean)
      .slice(0, 3);
    return { chips, sentence: rest || clean };
  }
  const first = clean.split(/(?<=[.!?])\s/)[0] ?? clean;
  const chips = first
    .replace(/[.!?]$/, '')
    .split(/,|;| y | pero | aunque /)
    .map((part) => part.trim())
    .filter((part) => part.length >= 6 && part.length <= 40 && part.split(' ').length <= 6)
    .slice(0, 3)
    .map(capitalize);
  return { chips: chips.length >= 2 ? chips : [], sentence: clean };
}
