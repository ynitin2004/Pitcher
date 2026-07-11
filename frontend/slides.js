// The 6-slide deck on the Basics of Machine Learning.
//
// This is the single source of truth for slide content. Later phases feed this
// same text to gpt-realtime (so the model knows what each slide says) and the
// model's `go_to_slide(n)` tool call flips `index.html` to slide n.
//
// Each slide: { title, bullets[], note } — `note` is a short speaker line.

const SLIDES = [
  {
    title: "What is Machine Learning?",
    bullets: [
      "Software that learns patterns from data instead of being explicitly programmed",
      "You show it examples; it figures out the rule",
      "Powers recommendations, spam filters, voice assistants, and more",
    ],
    note: "The big idea: learn from data, not hand-written rules.",
  },
  {
    title: "Types of ML",
    bullets: [
      "Supervised — learns from labeled examples (input → known answer)",
      "Unsupervised — finds structure in unlabeled data (clusters, groups)",
      "Reinforcement — learns by trial and error via rewards",
    ],
    note: "Three families, split by what kind of feedback the model gets.",
  },
  {
    title: "How a Model Learns",
    bullets: [
      "Training data goes in; the model adjusts internal parameters",
      "It measures error (loss) and nudges itself to reduce it",
      "Repeat over many examples until predictions get good",
    ],
    note: "Training = repeatedly reducing the gap between guess and truth.",
  },
  {
    title: "Common Algorithms",
    bullets: [
      "Linear / logistic regression — simple, fast baselines",
      "Decision trees & random forests — rules that split the data",
      "Neural networks — layered nodes that model complex patterns",
    ],
    note: "Pick the simplest algorithm that solves the problem well.",
  },
  {
    title: "Real-World Use Cases",
    bullets: [
      "Healthcare — flag anomalies in medical scans",
      "Finance — detect fraudulent transactions",
      "Everyday — translation, search, recommendations, self-driving",
    ],
    note: "ML is already woven into apps you use every day.",
  },
  {
    title: "Limitations & Ethics",
    bullets: [
      "Only as good as its data — garbage in, garbage out",
      "Can inherit and amplify bias present in the data",
      "Needs transparency, privacy care, and human oversight",
    ],
    note: "Powerful, but not neutral — use it responsibly.",
  },
];

// Expose for both plain <script> use now and module use later.
if (typeof window !== "undefined") {
  window.SLIDES = SLIDES;
}
