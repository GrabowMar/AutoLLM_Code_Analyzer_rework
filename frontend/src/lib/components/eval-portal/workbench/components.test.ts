// Server-side renders of the workbench components with realistic payloads: catches
// template runtime errors and pins the safety rules (iframe sandbox, tool labels
// only, untrusted strings as text, A/B as given by the server).
import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import type {
  Block,
  HostingInfo,
  Observation,
  PortalApp,
  ReviewFinding,
} from "$lib/api/types/human-eval";
import AppFrame from "./AppFrame.svelte";
import AppPane from "./AppPane.svelte";
import BugsForm from "./BugsForm.svelte";
import FindingReview from "./FindingReview.svelte";
import HostingCard from "./HostingCard.svelte";
import ObservationList from "./ObservationList.svelte";
import PairwiseForm from "./PairwiseForm.svelte";
import QuestionnaireForm from "./QuestionnaireForm.svelte";
import RatingForm from "./RatingForm.svelte";
import RequirementsForm from "./RequirementsForm.svelte";
import ScenariosForm from "./ScenariosForm.svelte";
import SecurityForm from "./SecurityForm.svelte";
import SpecPanel from "./SpecPanel.svelte";
import StepNav from "./StepNav.svelte";
import SubmitBar from "./SubmitBar.svelte";
import { BlockAutosave } from "./autosave.svelte";
import { frameDecision } from "./hosting";

const PORTAL = "https://lab.example.org";
const noop = () => {};

type VerdictEntry = { verdict: "tp" | "fp" | "unsure"; note: string };

/** The workbench-owned verdict saver FindingReview writes through. */
function verdictSaver(
  save: (
    id: string,
    entry: VerdictEntry,
  ) => Promise<unknown> = async () => ({}),
) {
  return new BlockAutosave<VerdictEntry>(save);
}

function hosting(over: Partial<HostingInfo> = {}): HostingInfo {
  return {
    state: "idle",
    url: null,
    progress: null,
    message: "",
    error: "",
    ...over,
  };
}

function app(
  slot: PortalApp["slot"],
  label: string,
  over: Partial<PortalApp> = {},
): PortalApp {
  return {
    slot,
    label,
    hosting: hosting(),
    spec: null,
    admin_credentials: null,
    has_source: false,
    ...over,
  };
}

function finding(over: Partial<ReviewFinding> = {}): ReviewFinding {
  return {
    id: "f1",
    tool: "bandit",
    tool_name: "Bandit",
    tool_severity: "",
    category: "security",
    rule_id: "B105",
    title: "Hardcoded password",
    description: "A password <script>alert(1)</script> is hardcoded.",
    file_path: "backend/app.py",
    line: 12,
    code_snippet: "PASSWORD = 'x'",
    docs_url: "javascript:alert(1)",
    cwe: "CWE-259",
    url: "",
    method: "",
    param: "",
    evidence: "",
    ...over,
  };
}

function observation(over: Partial<Observation> = {}): Observation {
  return {
    id: "o1",
    assignment_id: "a1",
    app_slot: "single",
    block_id: "bugs",
    kind: "bug",
    title: "Crash <b>bold</b>",
    description: "",
    steps: "",
    location: "javascript:alert(1)",
    category: "crash",
    severity: "high",
    cwe: "",
    checklist_item_id: "",
    attachments: [],
    created_at: "",
    updated_at: "",
    ...over,
  };
}

describe("AppFrame", () => {
  it("frames a cross-origin https app in the sandbox without a referrer", () => {
    const url = "https://llm-ab12.apps.example.org/";
    const { body } = render(AppFrame, {
      props: {
        app: { slot: "single", label: "App 01" },
        hosting: hosting({ state: "running", url }),
        decision: frameDecision(url, PORTAL),
      },
    });
    expect(body).toContain("<iframe");
    expect(body).toContain(
      'sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads"',
    );
    expect(body).toContain('referrerpolicy="no-referrer"');
    expect(body).not.toContain("allow-top-navigation");
  });

  it("offers only a new tab for plain-http apps", () => {
    const url = "http://llm-ab12.apps.example.org/";
    const { body } = render(AppFrame, {
      props: {
        app: { slot: "single", label: "App 01" },
        hosting: hosting({ state: "running", url }),
        decision: frameDecision(url, PORTAL),
      },
    });
    expect(body).not.toContain("<iframe");
    expect(body).toContain('rel="noopener noreferrer"');
    expect(body).toContain('target="_blank"');
    expect(body).toContain("Open in new tab");
  });

  it("shows queue state and a start button while idle", () => {
    const queued = render(HostingCard, {
      props: {
        app: { slot: "A", label: "App 02" },
        hosting: hosting({
          state: "queued",
          message: "Waiting for a free app slot (2nd in line)",
        }),
        decision: frameDecision(null, PORTAL),
      },
    }).body;
    expect(queued).toContain("A · App 02");
    expect(queued).toContain("2nd in line");
    const idle = render(HostingCard, {
      props: {
        app: { slot: "single", label: "App 02" },
        hosting: hosting(),
        decision: frameDecision(null, PORTAL),
        onstart: noop,
      },
    }).body;
    expect(idle).toContain("Start app");
    const readOnly = render(HostingCard, {
      props: {
        app: { slot: "single", label: "App 02" },
        hosting: hosting(),
        decision: frameDecision(null, PORTAL),
        onstart: noop,
        readOnly: true,
      },
    }).body;
    expect(readOnly).not.toContain("Start app");
  });
});

describe("FindingReview", () => {
  it("shows the tool's own label, 'unrated' when blank, and never links unsafe docs", () => {
    const { body } = render(FindingReview, {
      props: {
        saver: verdictSaver(),
        findingsBySlot: {
          single: [
            finding(),
            finding({
              id: "f2",
              tool_severity: "HIGH",
              docs_url: "https://docs.example/b105",
            }),
          ],
        },
        slotLabels: { single: "App 01" },
        verdicts: { f2: { verdict: "tp", note: "real" } },
        onverdict: noop,
      },
    });
    expect(body).toContain("unrated");
    expect(body).toContain("HIGH");
    expect(body).toContain("Bandit");
    expect(body).toContain("backend/app.py:12");
    expect(body).not.toContain('href="javascript:');
    expect(body).toContain('href="https://docs.example/b105"');
    expect(body).not.toContain("<script>alert(1)</script>");
    expect(body).toContain("&lt;script>");
    expect(body).toContain("1/2");
  });

  // C08: verdicts and notes go through the workbench's saver (which submit and
  // skip flush), so its state is what the list shows.
  it("writes through the saver it is given and shows that saver's state", async () => {
    const saver = verdictSaver(async () => {
      throw { detail: "offline" };
    });
    saver.schedule("f1", { verdict: "fp", note: "escaped by the ORM" });
    expect(await saver.flush()).toBe(false);
    const { body } = render(FindingReview, {
      props: {
        saver,
        findingsBySlot: { single: [finding()] },
        slotLabels: { single: "App 01" },
        verdicts: { f1: { verdict: "fp", note: "escaped by the ORM" } },
        onverdict: noop,
      },
    });
    expect(body).toContain("Not saved — retry");
  });
});

describe("ObservationList", () => {
  it("renders participant text as text and links only http(s) locations", () => {
    const { body } = render(ObservationList, {
      props: {
        assignmentId: "a1",
        kind: "bug",
        slot: "single",
        blockId: "bugs",
        appLabel: "App 01",
        observations: [
          observation(),
          observation({
            id: "o2",
            location: "https://llm-1.apps.example.org/admin",
            severity: "",
          }),
        ],
        onsaved: noop,
        ondeleted: noop,
      },
    });
    expect(body).not.toContain("<b>bold</b>");
    expect(body).not.toContain('href="javascript:');
    expect(body).toContain('href="https://llm-1.apps.example.org/admin"');
    expect(body).toContain("Your severity: High");
    expect(body).toContain("Report a bug");
  });
});

describe("forms", () => {
  it("renders a rating block with anchors and N/A", () => {
    const block: Block = {
      id: "q",
      type: "rating",
      title: "Quality",
      comment: "required",
      criteria: [
        {
          id: "ux",
          label: "Usability",
          scale: { min: 1, max: 7, min_label: "Poor", max_label: "Great" },
          required: true,
          allow_na: true,
        },
      ],
    };
    const { body } = render(RatingForm, {
      props: { block, answers: { criteria: { ux: 5 } }, onchange: noop },
    });
    expect(body).toContain("Usability");
    expect(body).toContain("Can't judge");
    expect(body).toContain("7 = Great");
    expect(body).toContain("Comment");
  });

  it("groups requirements and offers blocked vs can't test", () => {
    const block: Block = {
      id: "r",
      type: "requirements",
      title: "Reqs",
      sources: ["api"],
      custom: [],
      note: "required_unless_met",
    };
    const { body } = render(RequirementsForm, {
      props: {
        block,
        answers: { items: { ep_1: { verdict: "partial" } } },
        items: [
          { id: "ep_1", source: "api", text: "GET /api/todos — list" },
          { id: "c_1", source: "custom", text: "Looks tidy" },
        ],
        onchange: noop,
      },
    });
    expect(body).toContain("API endpoints");
    expect(body).toContain("Additional checks");
    expect(body).toContain("Blocked");
    expect(body).toContain("Can't test");
    expect(body).toContain("(required)");
  });

  it("renders the security block with checklist, reports and findings", () => {
    const block: Block = {
      id: "sec",
      type: "security",
      title: "Security",
      checklist: [
        {
          id: "idor",
          label: "Access control",
          help: "Change ids in URLs",
          category: "A01:2021",
        },
      ],
      reports: true,
      finding_review: {
        enabled: true,
        max_findings: 5,
        categories: [],
        include_ai: false,
        include_dependency: false,
        include_dynamic: false,
      },
      http_console: true,
    };
    const { body } = render(SecurityForm, {
      props: {
        assignmentId: "a1",
        block,
        answers: { checklist: {} },
        context: { findings: { single: [finding()] } },
        apps: [app("single", "App 01")],
        observations: [],
        verdicts: {},
        verdictSaver: verdictSaver(),
        consoleAvailable: true,
        onchange: noop,
        onobservationsaved: noop,
        onobservationdeleted: noop,
        onverdict: noop,
      },
    });
    expect(body).toContain("Access control");
    expect(body).toContain("How to test");
    expect(body).toContain("Report a vulnerability");
    expect(body).toContain("HTTP console");
    expect(body).toContain("unrated");
  });

  it("renders timed scenarios with the ease question once an outcome exists", () => {
    const block: Block = {
      id: "ux",
      type: "scenarios",
      title: "Tasks",
      timed: true,
      ease_question: true,
      scenarios: [
        { id: "s1", title: "Sign up", description: "Create an account" },
      ],
    };
    const { body } = render(ScenariosForm, {
      props: {
        block,
        answers: { scenarios: { s1: { outcome: "success", seconds: 65 } } },
        onchange: noop,
      },
    });
    expect(body).toContain("1:05");
    expect(body).toContain("Very easy");
    expect(body).toContain("Resume");
  });

  it("renders questionnaire questions through QuestionField", () => {
    const block: Block = {
      id: "sus",
      type: "questionnaire",
      title: "SUS",
      instrument: "sus",
      questions: [],
    };
    const { body } = render(QuestionnaireForm, {
      props: {
        block,
        answers: { answers: {} },
        questions: [
          {
            id: "q1",
            kind: "scale",
            label: "I would use this often",
            required: true,
            scale: { min: 1, max: 5 },
          },
        ],
        onchange: noop,
      },
    });
    expect(body).toContain("I would use this often");
  });

  it("asks per app for bugs or 'none found'", () => {
    const block: Block = {
      id: "bugs",
      type: "bugs",
      title: "Bugs",
      min_reports: 0,
    };
    const { body } = render(BugsForm, {
      props: {
        assignmentId: "a1",
        block,
        answers: { none_found: { A: true } },
        apps: [app("A", "App 03"), app("B", "App 07")],
        observations: [observation({ app_slot: "B" })],
        onchange: noop,
        onobservationsaved: noop,
        onobservationdeleted: noop,
      },
    });
    expect(body).toContain("A · App 03");
    expect(body).toContain("B · App 07");
    expect(body).toContain("found no bugs");
  });

  it("labels pairwise sides as given by the server", () => {
    const block: Block = {
      id: "ab",
      type: "pairwise",
      title: "A vs B",
      criteria: [{ id: "ux", label: "Ease of use" }],
      scale: "five_point",
      overall: true,
      rationale: "optional",
    };
    const { body } = render(PairwiseForm, {
      props: {
        block,
        answers: { criteria: { ux: -1 } },
        apps: [app("A", "App 09"), app("B", "App 02")],
        onchange: noop,
      },
    });
    expect(body.indexOf("App 09")).toBeLessThan(body.indexOf("App 02"));
    expect(body).toContain("Overall, which app is better?");
    expect(body).toContain("A is slightly better");
  });
});

describe("panes and chrome", () => {
  it("renders the spec and admin login as text", () => {
    const { body } = render(SpecPanel, {
      props: {
        app: app("single", "App 01", {
          spec: {
            name: "Todo",
            description: "A <i>todo</i> app",
            backend_requirements: ["Store todos"],
            frontend_requirements: [],
            admin_requirements: [],
            api_endpoints: [
              {
                method: "GET",
                path: "/api/todos",
                description: "List",
                auth: "user",
              },
            ],
            admin_api_endpoints: [],
          },
          admin_credentials: { username: "admin", password: "admin" },
        }),
      },
    });
    expect(body).toContain("Admin login for App 01");
    expect(body).toContain("/api/todos");
    expect(body).not.toContain("<i>todo</i>");
  });

  it("stacks apps as new-tab cards on phones and frames them on desktop", () => {
    const url = "https://llm-1.apps.example.org/";
    const apps = [app("A", "App 01", { has_source: true }), app("B", "App 02")];
    const common = {
      assignmentId: "a1",
      apps,
      hosting: {
        A: hosting({ state: "running", url }),
        B: hosting({ state: "building", progress: 40 }),
      },
      starting: {},
      decisions: {
        A: frameDecision(url, PORTAL),
        B: frameDecision(null, PORTAL),
      },
      consoleEnabled: true,
      focusSlot: "A" as const,
      sideBySide: false,
      view: "app" as const,
      consoleOpen: false,
      onstart: noop,
      onopen: noop,
    };
    const phone = render(AppPane, {
      props: { ...common, desktop: false },
    }).body;
    expect(phone).not.toContain("<iframe");
    expect(phone).toContain("Open in new tab");
    expect(phone).toContain("40%");
    const desk = render(AppPane, { props: { ...common, desktop: true } }).body;
    expect(desk).toContain("<iframe");
    expect(desk).toContain("Console");
    expect(desk).toContain("Source");
  });

  it("shows step state and the save indicator", () => {
    const nav = render(StepNav, {
      props: {
        steps: [
          { id: "a", title: "Ratings", complete: true, hasErrors: false },
          { id: "b", title: "Bugs", complete: false, hasErrors: true },
        ],
        active: 1,
        onselect: noop,
      },
    }).body;
    expect(nav).toContain("1/2 complete");
    expect(nav).toContain('aria-current="step"');
    expect(nav).toContain("(needs attention)");
    const bar = render(SubmitBar, {
      props: {
        index: 0,
        count: 2,
        saveState: "error",
        savedAt: null,
        completeCount: 1,
        allowSkip: true,
        onprev: noop,
        onnext: noop,
        onretry: noop,
        onskip: noop,
        onsubmit: noop,
      },
    }).body;
    expect(bar).toContain("Not saved");
    expect(bar).toContain("Skip");
    expect(bar).toContain("Submit");
    const ro = render(SubmitBar, {
      props: {
        index: 1,
        count: 2,
        saveState: "saved",
        savedAt: null,
        completeCount: 2,
        readOnly: true,
        onprev: noop,
        onnext: noop,
        onretry: noop,
        onskip: noop,
        onsubmit: noop,
      },
    }).body;
    expect(ro).toContain("Read-only");
    expect(ro).not.toContain("Submit");
  });
});
