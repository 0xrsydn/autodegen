import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { parseFrontmatter } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";

type AgentName =
  | "historian"
  | "bottleneck"
  | "trend-scout"
  | "breakout-scout"
  | "meanrev-scout"
  | "regime-scout"
  | "asymmetry-scout"
  | "volstate-scout"
  | "pullback-scout"
  | "exhaustion-scout"
  | "risk-exit-scout"
  | "critic"
  | "executor";

interface AgentConfig {
  name: AgentName;
  description: string;
  tools?: string[];
  provider?: string;
  model?: string;
  systemPrompt: string;
  filePath: string;
}

interface AgentRunResult {
  agent: AgentName;
  task: string;
  output: string;
  stderr: string;
  exitCode: number;
}

interface ResultsRow {
  [key: string]: string;
}

interface IterationSummary {
  iteration: number;
  hypothesis: string;
  accepted: boolean;
  reason: string;
  latestComposite?: number;
  latestStatus?: string;
}

type FamilyName =
  | "trend"
  | "breakout"
  | "mean_reversion"
  | "regime_switching"
  | "asymmetry"
  | "volatility_state"
  | "pullback_continuation"
  | "exhaustion_recovery"
  | "risk_exit";

interface AntiRepetitionPolicy {
  blockedFamily: FamilyName | null;
  reason: string;
}

const SIMPLE_SCOUTS: AgentName[] = ["trend-scout", "breakout-scout", "meanrev-scout"];
const BROAD_SCOUTS: AgentName[] = [
  "regime-scout",
  "asymmetry-scout",
  "volstate-scout",
  "pullback-scout",
  "exhaustion-scout",
  "risk-exit-scout",
];

const REQUIRED_AGENTS: AgentName[] = [
  "historian",
  "bottleneck",
  ...SIMPLE_SCOUTS,
  ...BROAD_SCOUTS,
  "critic",
  "executor",
];

const degenSwarmParams = Type.Object({
  goal: Type.Optional(
    Type.String({
      description: "Optional extra goal or steering note for the swarm.",
    }),
  ),
  mode: Type.Optional(
    Type.String({
      description:
        'Use "research" for hypothesis ranking only, or "execute" to also edit strategy.py, validate, evaluate, and accept/revert.',
      default: "execute",
    }),
  ),
  iterations: Type.Optional(
    Type.Integer({
      description: "How many serialized experimentation iterations to run.",
      minimum: 1,
      maximum: 20,
      default: 1,
    }),
  ),
  commitOnAccept: Type.Optional(
    Type.Boolean({
      description: "Commit accepted improvements with the required hypothesis commit message.",
      default: true,
    }),
  ),
});

function findNearestPiDir(cwd: string): string | null {
  let current = cwd;
  while (true) {
    const candidate = path.join(current, ".pi");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) return candidate;
    const parent = path.dirname(current);
    if (parent === current) return null;
    current = parent;
  }
}

function loadAgents(cwd: string): Map<AgentName, AgentConfig> {
  const piDir = findNearestPiDir(cwd);
  if (!piDir) throw new Error("Could not find project .pi directory.");

  const agentsDir = path.join(piDir, "agents");
  if (!fs.existsSync(agentsDir)) {
    throw new Error(`Missing agents directory: ${agentsDir}`);
  }

  const out = new Map<AgentName, AgentConfig>();
  for (const entry of fs.readdirSync(agentsDir, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".md")) continue;
    const filePath = path.join(agentsDir, entry.name);
    const raw = fs.readFileSync(filePath, "utf-8");
    const { frontmatter, body } = parseFrontmatter<Record<string, unknown>>(raw);
    const name = String(frontmatter.name || "") as AgentName;
    if (!name) continue;

    const rawTools = frontmatter.tools;
    const tools = Array.isArray(rawTools)
      ? rawTools.map((value) => String(value).trim()).filter(Boolean)
      : String(rawTools || "")
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean);

    out.set(name, {
      name,
      description: String(frontmatter.description || name),
      tools: tools.length > 0 ? tools : undefined,
      provider: frontmatter.provider ? String(frontmatter.provider) : undefined,
      model: frontmatter.model ? String(frontmatter.model) : undefined,
      systemPrompt: body.trim(),
      filePath,
    });
  }

  for (const name of REQUIRED_AGENTS) {
    if (!out.has(name)) throw new Error(`Missing required agent definition: ${name}`);
  }

  return out;
}

function writeTempPrompt(agentName: string, prompt: string): { dir: string; filePath: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-autodegen-"));
  const safeName = agentName.replace(/[^a-zA-Z0-9_.-]/g, "_");
  const filePath = path.join(dir, `${safeName}.md`);
  fs.writeFileSync(filePath, prompt, { encoding: "utf-8", mode: 0o600 });
  return { dir, filePath };
}

function extractAssistantText(message: any): string {
  if (!message || message.role !== "assistant" || !Array.isArray(message.content)) return "";
  return message.content
    .filter((part: any) => part?.type === "text" && typeof part.text === "string")
    .map((part: any) => part.text)
    .join("\n")
    .trim();
}

async function runPiAgent(
  cwd: string,
  agent: AgentConfig,
  task: string,
  signal?: AbortSignal,
): Promise<AgentRunResult> {
  const args: string[] = [
    "--mode",
    "json",
    "-p",
    "--no-session",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
  ];

  if (agent.provider) args.push("--provider", agent.provider);
  if (agent.model) args.push("--model", agent.model);
  if (agent.tools && agent.tools.length > 0) args.push("--tools", agent.tools.join(","));

  let tmp: { dir: string; filePath: string } | null = null;
  if (agent.systemPrompt) {
    tmp = writeTempPrompt(agent.name, agent.systemPrompt);
    args.push("--append-system-prompt", tmp.filePath);
  }

  args.push(`Task:\n${task}`);

  try {
    return await new Promise<AgentRunResult>((resolve) => {
      const proc = spawn("pi", args, {
        cwd,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PI_SKIP_VERSION_CHECK: "1" },
      });

      let stdout = "";
      let stderr = "";
      let lastAssistant = "";
      let aborted = false;

      const processLine = (line: string) => {
        if (!line.trim()) return;
        try {
          const event = JSON.parse(line);
          if (event.type === "message_end") {
            const text = extractAssistantText(event.message);
            if (text) lastAssistant = text;
          }
        } catch {
          // ignore non-JSON noise
        }
      };

      proc.stdout.on("data", (chunk) => {
        const text = chunk.toString();
        stdout += text;
        const lines = stdout.split("\n");
        stdout = lines.pop() || "";
        for (const line of lines) processLine(line);
      });

      proc.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });

      proc.on("close", (code) => {
        if (stdout.trim()) processLine(stdout);
        resolve({
          agent: agent.name,
          task,
          output: lastAssistant.trim(),
          stderr: stderr.trim(),
          exitCode: aborted ? 130 : code ?? 1,
        });
      });

      proc.on("error", (error) => {
        resolve({
          agent: agent.name,
          task,
          output: "",
          stderr: String(error),
          exitCode: 1,
        });
      });

      if (signal) {
        const abort = () => {
          aborted = true;
          proc.kill("SIGTERM");
          setTimeout(() => {
            if (!proc.killed) proc.kill("SIGKILL");
          }, 3000);
        };
        if (signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      }
    });
  } finally {
    if (tmp) {
      try {
        fs.unlinkSync(tmp.filePath);
      } catch {}
      try {
        fs.rmdirSync(tmp.dir);
      } catch {}
    }
  }
}

const SNAPSHOT_SKIP_DIRS = new Set([".git", "__pycache__", ".venv", "tmp", "node_modules"]);

function readFileIfExists(filePath: string): string {
  try {
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return "";
  }
}

function snapshotRepo(cwd: string): Map<string, string> {
  const files = new Map<string, string>();

  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (SNAPSHOT_SKIP_DIRS.has(entry.name)) continue;
      const absolute = path.join(dir, entry.name);
      const relative = path.relative(cwd, absolute).replace(/\\/g, "/");
      if (entry.isDirectory()) {
        walk(absolute);
        continue;
      }
      if (!entry.isFile()) continue;
      files.set(relative, fs.readFileSync(absolute, "utf-8"));
    }
  };

  walk(cwd);
  return files;
}

function diffSnapshots(before: Map<string, string>, after: Map<string, string>): string[] {
  const changed = new Set<string>();
  for (const [filePath, content] of after.entries()) {
    if (before.get(filePath) !== content) changed.add(filePath);
  }
  for (const filePath of before.keys()) {
    if (!after.has(filePath)) changed.add(filePath);
  }
  return Array.from(changed).sort();
}

function restoreSnapshotPaths(cwd: string, snapshot: Map<string, string>, paths: string[]): void {
  for (const filePath of paths) {
    const absolute = path.join(cwd, filePath);
    const prior = snapshot.get(filePath);
    if (prior === undefined) {
      fs.rmSync(absolute, { recursive: true, force: true });
      continue;
    }
    fs.mkdirSync(path.dirname(absolute), { recursive: true });
    fs.writeFileSync(absolute, prior, "utf-8");
  }
}

function parseResults(content: string): ResultsRow[] {
  const trimmed = content.trim();
  if (!trimmed) return [];
  const lines = trimmed.split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return [];
  const headers = lines[0].split("\t");
  return lines.slice(1).map((line) => {
    const cols = line.split("\t");
    const row: ResultsRow = {};
    headers.forEach((header, index) => {
      row[header] = cols[index] ?? "";
    });
    return row;
  });
}

function parseNumber(value: string | undefined): number | undefined {
  if (value === undefined || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function getBestPassingComposite(rows: ResultsRow[]): number | null {
  let best: number | null = null;
  for (const row of rows) {
    if (row.status !== "PASS") continue;
    const composite = parseNumber(row.composite);
    if (composite === undefined) continue;
    if (best === null || composite > best) best = composite;
  }
  return best;
}

function getFailureStreak(rows: ResultsRow[]): number {
  let streak = 0;
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].status === "PASS") break;
    streak += 1;
  }
  return streak;
}

function getExplorationPolicy(rows: ResultsRow[]): {
  failureStreak: number;
  preference: "prefer_simple" | "pivot_broad";
  guidance: string;
} {
  const failureStreak = getFailureStreak(rows);
  if (failureStreak >= 5) {
    return {
      failureStreak,
      preference: "pivot_broad",
      guidance:
        "There are 5+ failed experiments in a row. Per repo policy, pivot away from minor tweaks and prefer broader archetypes unless a simple candidate is clearly superior.",
    };
  }
  return {
    failureStreak,
    preference: "prefer_simple",
    guidance:
      "When candidates are close, prefer the simpler trend / breakout / mean-reversion families. Only choose broader archetypes when they clearly target the bottleneck better.",
  };
}

function familyForAgent(agent: AgentName): FamilyName | null {
  switch (agent) {
    case "trend-scout":
      return "trend";
    case "breakout-scout":
      return "breakout";
    case "meanrev-scout":
      return "mean_reversion";
    case "regime-scout":
      return "regime_switching";
    case "asymmetry-scout":
      return "asymmetry";
    case "volstate-scout":
      return "volatility_state";
    case "pullback-scout":
      return "pullback_continuation";
    case "exhaustion-scout":
      return "exhaustion_recovery";
    case "risk-exit-scout":
      return "risk_exit";
    default:
      return null;
  }
}

function classifyRowFamily(row: ResultsRow): FamilyName | null {
  const haystack = `${row.name || ""} ${row.description || ""}`.toLowerCase();
  const has = (...needles: string[]) => needles.some((needle) => haystack.includes(needle));

  if (has("zscore", "mean_reversion", "mean reversion", "reversal", "revert", "rsi")) return "mean_reversion";
  if (has("donchian", "breakout", "channel")) return "breakout";
  if (has("pullback", "retest", "reclaim")) return "pullback_continuation";
  if (has("regime", "state-machine", "state machine", "switch")) return "regime_switching";
  if (has("asymmetric", "asymmetry", "long-only", "long only", "short-only", "short only")) return "asymmetry";
  if (has("volstate", "volatility_state", "volatility state", "compression", "expansion", "shock", "cooldown")) return "volatility_state";
  if (has("exhaustion", "overshoot", "panic", "crash-recovery", "crash recovery", "liquidation")) return "exhaustion_recovery";
  if (has("time stop", "trailing", "trail", "risk", "exit", "exposure", "sizing", "risk_exit")) return "risk_exit";
  if (has("ema", "adx", "trend", "momentum", "crossover")) return "trend";
  return null;
}

function getAntiRepetitionPolicy(rows: ResultsRow[], recentWinnerAgents: AgentName[]): AntiRepetitionPolicy {
  const recentWinnerFamilies = recentWinnerAgents
    .slice(-3)
    .map((agent) => familyForAgent(agent))
    .filter((family): family is FamilyName => family !== null);

  if (
    recentWinnerFamilies.length >= 2 &&
    recentWinnerFamilies[recentWinnerFamilies.length - 1] === recentWinnerFamilies[recentWinnerFamilies.length - 2]
  ) {
    const blockedFamily = recentWinnerFamilies[recentWinnerFamilies.length - 1];
    return {
      blockedFamily,
      reason: `This swarm already picked the ${blockedFamily} family in the last two iterations. Force diversity unless that family is overwhelmingly superior.`,
    };
  }

  const recentRows = rows.slice(-5);
  const counts = new Map<FamilyName, number>();
  for (const row of recentRows) {
    const family = classifyRowFamily(row);
    if (!family) continue;
    counts.set(family, (counts.get(family) ?? 0) + 1);
  }

  let blockedFamily: FamilyName | null = null;
  let blockedCount = 0;
  for (const [family, count] of counts.entries()) {
    if (count > blockedCount) {
      blockedFamily = family;
      blockedCount = count;
    }
  }

  if (blockedFamily && blockedCount >= 3) {
    return {
      blockedFamily,
      reason: `The recent ledger is dominated by ${blockedFamily} ideas (${blockedCount} of last ${recentRows.length} rows). Avoid repeating that family unless clearly superior.`,
    };
  }

  return {
    blockedFamily: null,
    reason: "No anti-repetition block is active; family diversity looks acceptable.",
  };
}

function getLatestRow(rows: ResultsRow[]): ResultsRow | undefined {
  return rows.length > 0 ? rows[rows.length - 1] : undefined;
}

function summarizeRow(row: ResultsRow | undefined): string {
  if (!row) return "no recorded result";
  const parts = [
    `status=${row.status || "?"}`,
    `composite=${row.composite || "?"}`,
    `wf=${row.bar_sharpe_wf || "?"}`,
    `val=${row.bar_sharpe_val || "?"}`,
    `decay=${row.decay || "?"}`,
    `maxdd_wf=${row.maxdd_wf || "?"}`,
    `maxdd_val=${row.maxdd_val || "?"}`,
    `trades_wf=${row.trades_wf || "?"}`,
    `trades_val=${row.trades_val || "?"}`,
  ];
  return parts.join(", ");
}

function tailLines(text: string, count: number): string {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length <= count) return lines.join("\n");
  return lines.slice(-count).join("\n");
}

function extractHypothesis(text: string): string {
  const match = text.match(/^HYPOTHESIS:\s*(.+)$/im);
  if (match) return match[1].trim().slice(0, 180);
  const line = text
    .split(/\r?\n/)
    .map((value) => value.trim())
    .find(Boolean);
  return (line || "improve robustness with a new hypothesis").slice(0, 180);
}

function extractWinnerAgent(text: string): AgentName | null {
  const match = text.match(/^WINNER:\s*(.+)$/im);
  if (!match) return null;
  const winner = match[1].trim() as AgentName;
  return REQUIRED_AGENTS.includes(winner) ? winner : null;
}

function formatGoal(goal: string | undefined): string {
  return goal?.trim()
    ? `Additional user goal: ${goal.trim()}`
    : "Additional user goal: none. Improve the strategy using the current bottleneck.";
}

function formatScoutPreference(preference: "prefer_simple" | "pivot_broad"): string {
  if (preference === "pivot_broad") {
    return `Favor broader archetypes first: ${BROAD_SCOUTS.join(", ")}. Only keep a simple scout winner if it is clearly stronger.`;
  }
  return `If candidates are close, favor simpler archetypes first: ${SIMPLE_SCOUTS.join(", ")}.`;
}

function formatAntiRepetitionPolicy(policy: AntiRepetitionPolicy): string {
  if (!policy.blockedFamily) return policy.reason;
  return `Anti-repetition guard: avoid ${policy.blockedFamily} for this selection round. ${policy.reason}`;
}

function agentFailed(result: AgentRunResult): boolean {
  return result.exitCode !== 0;
}

function agentFailureReason(result: AgentRunResult): string {
  return result.stderr || result.output || `exit code ${result.exitCode}`;
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

async function runCommand(
  pi: ExtensionAPI,
  cwd: string,
  command: string,
  args: string[],
  signal?: AbortSignal,
): Promise<{ code: number; stdout: string; stderr: string }> {
  const script = `cd ${shellQuote(cwd)} && ${[command, ...args].map(shellQuote).join(" ")}`;
  const result = await pi.exec("bash", ["-lc", script], { signal });
  return {
    code: result.code ?? 1,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
  };
}

async function restoreStrategy(strategyPath: string, original: string): Promise<void> {
  fs.writeFileSync(strategyPath, original, "utf-8");
}

function sendSwarmInstruction(pi: ExtensionAPI, idle: boolean, text: string) {
  if (idle) {
    pi.sendUserMessage(text);
    return;
  }
  pi.sendUserMessage(text, { deliverAs: "followUp" });
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "degen_swarm",
    label: "Degen Swarm",
    description:
      "Run the repo-local autodegen multi-agent loop. It uses specialized project agents to analyze results.tsv, generate candidate hypotheses, rank them, and optionally execute one official validate/evaluate/revert iteration.",
    promptSnippet:
      "Run the repo-local autodegen swarm for research or serialized experimentation loops.",
    promptGuidelines: [
      "Use degen_swarm instead of manually orchestrating multiple research/evaluation passes in this repository.",
      "Prefer mode=research when the user wants hypotheses only; prefer mode=execute for a guarded experimentation loop.",
    ],
    parameters: degenSwarmParams,

    async execute(_toolCallId, params, signal, onUpdate, ctx) {
      const piDir = findNearestPiDir(ctx.cwd);
      const cwd = piDir ? path.dirname(piDir) : ctx.cwd;
      const strategyPath = path.join(cwd, "strategy.py");
      const resultsPath = path.join(cwd, "results.tsv");
      const goal = params.goal?.trim() || "";
      const mode = params.mode === "research" ? "research" : "execute";
      const iterations = Math.max(1, Math.min(20, params.iterations ?? 1));
      const commitOnAccept = params.commitOnAccept ?? true;

      const log: string[] = [];
      const summaries: IterationSummary[] = [];
      const recentWinnerAgents: AgentName[] = [];
      const agents = loadAgents(cwd);

      const push = (line: string) => {
        log.push(line);
        onUpdate?.({ content: [{ type: "text", text: log.join("\n") }] });
      };

      push(`Loaded repo-local autodegen swarm (${mode}, ${iterations} iteration${iterations === 1 ? "" : "s"}).`);
      push(formatGoal(goal));

      for (let iteration = 1; iteration <= iterations; iteration++) {
        const header = `\n=== Iteration ${iteration}/${iterations} ===`;
        push(header);

        const strategyBefore = readFileIfExists(strategyPath);
        if (!strategyBefore) {
          throw new Error(`Missing strategy.py at ${strategyPath}`);
        }

        const snapshotBefore = snapshotRepo(cwd);
        const resultsBefore = parseResults(readFileIfExists(resultsPath));
        const previousBest = getBestPassingComposite(resultsBefore);
        const explorationPolicy = getExplorationPolicy(resultsBefore);
        const antiRepetitionPolicy = getAntiRepetitionPolicy(resultsBefore, recentWinnerAgents);
        push(`Previous best passing composite: ${previousBest === null ? "none" : previousBest.toFixed(6)}`);
        push(`Latest ledger row: ${summarizeRow(getLatestRow(resultsBefore))}`);
        push(`Failure streak: ${explorationPolicy.failureStreak}. ${explorationPolicy.guidance}`);
        push(formatAntiRepetitionPolicy(antiRepetitionPolicy));

        const [historian, bottleneck] = await Promise.all([
          runPiAgent(
            cwd,
            agents.get("historian")!,
            [
              formatGoal(goal),
              "Read results.tsv before anything else.",
              "Summarize the best passing run, the last five rows, repeated failure modes, and duplicate ideas to avoid.",
            ].join("\n\n"),
            signal,
          ),
          runPiAgent(
            cwd,
            agents.get("bottleneck")!,
            [
              formatGoal(goal),
              "Read results.tsv and strategy.py.",
              "Identify the single highest-leverage bottleneck for the next iteration and suggest what kind of structural change should address it.",
            ].join("\n\n"),
            signal,
          ),
        ]);

        const primaryFailure = [historian, bottleneck].find(agentFailed);
        if (primaryFailure) {
          const reason = `${primaryFailure.agent} failed: ${agentFailureReason(primaryFailure)}`;
          push(`Rejected: ${reason}`);
          summaries.push({ iteration, hypothesis: "n/a", accepted: false, reason });
          continue;
        }

        push(`Historian ready. ${extractHypothesis(historian.output)}`);
        push(`Bottleneck ready. ${extractHypothesis(bottleneck.output)}`);

        const researchContext = [
          formatGoal(goal),
          `Current exploration policy: ${explorationPolicy.guidance}`,
          formatScoutPreference(explorationPolicy.preference),
          formatAntiRepetitionPolicy(antiRepetitionPolicy),
          "Historian output:",
          historian.output || "(no output)",
          "Bottleneck output:",
          bottleneck.output || "(no output)",
          "Generate exactly one concrete strategy hypothesis in your assigned family.",
        ].join("\n\n");

        const scoutNames: AgentName[] = [...SIMPLE_SCOUTS, ...BROAD_SCOUTS];
        const scoutResults = await Promise.all(
          scoutNames.map((name) => runPiAgent(cwd, agents.get(name)!, researchContext, signal)),
        );

        const scoutFailure = scoutResults.find(agentFailed);
        if (scoutFailure) {
          const reason = `${scoutFailure.agent} failed: ${agentFailureReason(scoutFailure)}`;
          push(`Rejected: ${reason}`);
          summaries.push({ iteration, hypothesis: "n/a", accepted: false, reason });
          continue;
        }

        for (const scout of scoutResults) {
          push(`${scout.agent} candidate: ${extractHypothesis(scout.output)}`);
        }

        const criticPrompt = [
          formatGoal(goal),
          `Current exploration policy: ${explorationPolicy.guidance}`,
          formatScoutPreference(explorationPolicy.preference),
          `Current failure streak: ${explorationPolicy.failureStreak}`,
          formatAntiRepetitionPolicy(antiRepetitionPolicy),
          "Historian output:",
          historian.output || "(no output)",
          "Bottleneck output:",
          bottleneck.output || "(no output)",
          ...scoutResults.flatMap((result) => [
            `${result.agent} candidate:`,
            result.output || "(no output)",
          ]),
          "Choose exactly one winner. Prefer robust ideas, avoid near-duplicates, and keep the edit simple.",
        ].join("\n\n");

        const critic = await runPiAgent(cwd, agents.get("critic")!, criticPrompt, signal);

        if (agentFailed(critic)) {
          const reason = `critic failed: ${agentFailureReason(critic)}`;
          push(`Rejected: ${reason}`);
          summaries.push({ iteration, hypothesis: "n/a", accepted: false, reason });
          continue;
        }

        let criticOutput = critic.output;
        let winnerAgent = extractWinnerAgent(criticOutput);

        if (
          antiRepetitionPolicy.blockedFamily &&
          winnerAgent &&
          familyForAgent(winnerAgent) === antiRepetitionPolicy.blockedFamily
        ) {
          const filteredScoutResults = scoutResults.filter(
            (result) => familyForAgent(result.agent) !== antiRepetitionPolicy.blockedFamily,
          );
          if (filteredScoutResults.length > 0) {
            push(
              `Anti-repetition guard triggered on ${antiRepetitionPolicy.blockedFamily}; asking critic to choose the best alternative family.`,
            );
            const guardedCritic = await runPiAgent(
              cwd,
              agents.get("critic")!,
              [
                formatGoal(goal),
                `Current exploration policy: ${explorationPolicy.guidance}`,
                formatScoutPreference(explorationPolicy.preference),
                `Current failure streak: ${explorationPolicy.failureStreak}`,
                formatAntiRepetitionPolicy(antiRepetitionPolicy),
                "The prior winner came from an overused family. Re-select from the remaining candidate families only.",
                "Historian output:",
                historian.output || "(no output)",
                "Bottleneck output:",
                bottleneck.output || "(no output)",
                ...filteredScoutResults.flatMap((result) => [
                  `${result.agent} candidate:`,
                  result.output || "(no output)",
                ]),
                "Choose exactly one winner from the remaining families.",
              ].join("\n\n"),
              signal,
            );
            if (!agentFailed(guardedCritic)) {
              criticOutput = guardedCritic.output;
              winnerAgent = extractWinnerAgent(criticOutput);
            }
          }
        }

        if (winnerAgent) recentWinnerAgents.push(winnerAgent);

        const hypothesis = extractHypothesis(criticOutput);
        push(`Selected hypothesis: ${hypothesis}${winnerAgent ? ` [${winnerAgent}]` : ""}`);

        if (mode === "research") {
          summaries.push({
            iteration,
            hypothesis,
            accepted: false,
            reason: "research_only",
          });
          continue;
        }

        const executor = await runPiAgent(
          cwd,
          agents.get("executor")!,
          [
            formatGoal(goal),
            `HYPOTHESIS: ${hypothesis}`,
            "Historian output:",
            historian.output || "(no output)",
            "Bottleneck output:",
            bottleneck.output || "(no output)",
            "Critic decision:",
            criticOutput || "(no output)",
            "Edit strategy.py only. Do not run the evaluation harness yourself.",
          ].join("\n\n"),
          signal,
        );

        if (agentFailed(executor)) {
          const reason = `executor failed: ${agentFailureReason(executor)}`;
          await restoreStrategy(strategyPath, strategyBefore);
          push(`Rejected: ${reason}`);
          summaries.push({ iteration, hypothesis, accepted: false, reason });
          continue;
        }

        push(`Executor finished: ${extractHypothesis(executor.output)}`);

        const snapshotAfterEdit = snapshotRepo(cwd);
        const changedAfterEdit = diffSnapshots(snapshotBefore, snapshotAfterEdit);
        const illegalPaths = changedAfterEdit.filter((filePath) => filePath !== "strategy.py");
        if (illegalPaths.length > 0) {
          restoreSnapshotPaths(cwd, snapshotBefore, illegalPaths);
          await restoreStrategy(strategyPath, strategyBefore);
          const reason = `executor touched non-strategy files: ${illegalPaths.join(", ")}`;
          push(`Rejected: ${reason}`);
          summaries.push({ iteration, hypothesis, accepted: false, reason });
          continue;
        }

        const strategyAfterEdit = readFileIfExists(strategyPath);
        if (strategyAfterEdit === strategyBefore) {
          const reason = "executor made no change to strategy.py";
          push(`Rejected: ${reason}`);
          summaries.push({ iteration, hypothesis, accepted: false, reason });
          continue;
        }

        push("Running data validation...");
        const validate = await runCommand(pi, cwd, "uv", ["run", "python", "prepare.py", "validate"], signal);
        if (validate.code !== 0) {
          await restoreStrategy(strategyPath, strategyBefore);
          const reason = `validate failed (${validate.code})`;
          push(`Rejected: ${reason}`);
          push(tailLines(`${validate.stdout}\n${validate.stderr}`, 20));
          summaries.push({ iteration, hypothesis, accepted: false, reason });
          continue;
        }
        push("Validation passed.");

        push("Running official evaluation...");
        const evaluate = await runCommand(pi, cwd, "uv", ["run", "python", "strategy.py"], signal);
        const resultsAfter = parseResults(readFileIfExists(resultsPath));
        const latest = getLatestRow(resultsAfter);
        const latestComposite = parseNumber(latest?.composite);
        const latestStatus = latest?.status || "";

        push(`Latest evaluation row: ${summarizeRow(latest)}`);
        const latestIsNew = resultsAfter.length > resultsBefore.length;
        const improved = latestComposite !== undefined && (previousBest === null || latestComposite > previousBest);
        const accepted = evaluate.code === 0 && latestIsNew && latestStatus === "PASS" && improved;

        if (!accepted) {
          await restoreStrategy(strategyPath, strategyBefore);
          const reason =
            evaluate.code !== 0
              ? `evaluation command failed (${evaluate.code})`
              : !latestIsNew
                ? "results.tsv was not appended"
                : latestStatus !== "PASS"
                  ? `hard gates not passed (${latestStatus || "unknown"})`
                  : `composite did not beat previous best (${previousBest === null ? "none" : previousBest.toFixed(6)})`;
          push(`Rejected: ${reason}`);
          push(tailLines(`${evaluate.stdout}\n${evaluate.stderr}`, 30));
          summaries.push({
            iteration,
            hypothesis,
            accepted: false,
            reason,
            latestComposite,
            latestStatus,
          });
          continue;
        }

        push("Accepted improvement.");
        if (commitOnAccept) {
          const add = await runCommand(pi, cwd, "git", ["add", "strategy.py"], signal);
          const commit = await runCommand(pi, cwd, "git", ["commit", "-m", `hypothesis: ${hypothesis}`], signal);
          if (add.code === 0 && commit.code === 0) push("Committed accepted improvement.");
          else push(`Commit skipped or failed. ${tailLines(`${add.stderr}\n${commit.stderr}`, 10)}`.trim());
        }

        summaries.push({
          iteration,
          hypothesis,
          accepted: true,
          reason: "accepted",
          latestComposite,
          latestStatus,
        });
      }

      const acceptedCount = summaries.filter((item) => item.accepted).length;
      const rejectedCount = summaries.length - acceptedCount;
      const summaryLines = [
        `Completed ${summaries.length} iteration${summaries.length === 1 ? "" : "s"}.`,
        `Accepted: ${acceptedCount}`,
        `Rejected/research-only: ${rejectedCount}`,
        ...summaries.map((item) => {
          const composite = item.latestComposite === undefined ? "n/a" : item.latestComposite.toFixed(6);
          return `- #${item.iteration}: ${item.hypothesis} :: ${item.accepted ? "ACCEPTED" : item.reason} :: composite=${composite}`;
        }),
      ];

      return {
        content: [{ type: "text", text: [...log, "", ...summaryLines].join("\n") }],
        details: { mode, iterations, summaries },
      };
    },
  });

  pi.registerCommand("degen-once", {
    description: "Queue one guarded autodegen swarm iteration",
    handler: async (args, ctx) => {
      const goal = args?.trim() || "Improve strategy.py with one guarded autodegen iteration.";
      sendSwarmInstruction(
        pi,
        ctx.isIdle(),
        `Use the degen_swarm tool with mode=execute, iterations=1, commitOnAccept=true, goal=${JSON.stringify(goal)}. Summarize the outcome briefly when done.`,
      );
      ctx.ui.notify("Queued one degen swarm iteration.", "info");
    },
  });

  pi.registerCommand("degen-loop", {
    description: "Queue N guarded autodegen swarm iterations: /degen-loop <n> [goal]",
    handler: async (args, ctx) => {
      const trimmed = (args || "").trim();
      const match = trimmed.match(/^(\d+)\s*(.*)$/);
      const iterations = match ? Math.max(1, Math.min(20, Number(match[1]))) : 3;
      const goal = (match?.[2] || trimmed || "Run a guarded autodegen experimentation loop.").trim();
      sendSwarmInstruction(
        pi,
        ctx.isIdle(),
        `Use the degen_swarm tool with mode=execute, iterations=${iterations}, commitOnAccept=true, goal=${JSON.stringify(goal)}. Summarize accepted and rejected iterations briefly when done.`,
      );
      ctx.ui.notify(`Queued degen swarm loop (${iterations} iterations).`, "info");
    },
  });

  pi.registerCommand("degen-research", {
    description: "Queue a research-only autodegen swarm pass",
    handler: async (args, ctx) => {
      const goal = args?.trim() || "Produce ranked autodegen hypotheses without editing strategy.py.";
      sendSwarmInstruction(
        pi,
        ctx.isIdle(),
        `Use the degen_swarm tool with mode=research, iterations=1, goal=${JSON.stringify(goal)}. Return the chosen hypothesis and the alternatives.`,
      );
      ctx.ui.notify("Queued research-only degen swarm pass.", "info");
    },
  });
}
