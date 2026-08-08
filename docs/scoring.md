# Scoring

Four engines, one principle: **a score is never a bare number**. Each returns a
`ScoreResult` composed of named components that carry the reasons that produced
them, and the UI renders those reasons next to the number.

```python
@dataclass
class ScoreComponent:
    key: str; label: str
    points: float; max_points: float
    reasons: list[str]; matched: bool
```

Totals are normalised to 0–100 against the configured maximum, so changing the
weights rebalances the score without breaking the scale.

All weights and thresholds live in `scoring_configs` and are editable on the
Settings page. The values below are the defaults.

---

## 1. Job relevance (0–100)

| Component | Max | How it is earned |
| --- | --- | --- |
| Role match | 30 | Title matches a `ROLE` taxonomy term. Exact = full, alias = 95%, partial (token overlap ≥ 0.45) = proportional |
| Industry match | 20 | Company industry, description or name matches an `INDUSTRY` term |
| Experience match | 15 | Title seniority vs. seniority implied by `years_experience`; each level of distance costs 34%. Unstated seniority = 50% (neutral, not punished) |
| Skill match | 15 | `SKILL` terms found in title + description. Three strong skills = full |
| Location preference | 10 | Normalised location matches a `LOCATION` term. Unstated = 40% |
| Freshness | 10 | Full credit ≤ 24h, then linear decay to zero at 720h (30 days) |

**Hard exclusion.** If the title matches an `EXCLUDE` term, the job scores 0
with a stated reason (`"Title matches your excluded term 'Sales'"`). It is
excluded openly rather than buried under a low number.

### Worked example

```
Product Analyst — Razorpay (Fintech), Bengaluru, posted 14 hours ago

Relevance Score: 100/100
  ✓ Matches your target role 'Product Analyst'                      30/30
  ✓ Fintech company — one of your preferred domains                 20/20
  ✓ Seniority lines up with your experience level                   15/15
  ✓ Mentions skills from your profile: SQL, Product Analytics, A/B Testing   15/15
  ✓ Location matches your preference: Bangalore                     10/10
  ✓ Posted 14 hours ago                                             10/10
```

---

## 2. Hiring activity (0–100)

Answers "does this company look like it is hiring *right now*?" Each component
that fires also writes a row to `hiring_signals`, so the company page shows the
evidence, not just the number.

| Signal | Points | Fires when |
| --- | --- | --- |
| New relevant job | 25 | A relevant opening was discovered within 72h |
| Posted < 24 hours ago | 25 | The freshest relevant posting is under 24h old (partial credit up to 72h) |
| Multiple relevant openings | 20 | ≥ 2 relevant roles live at once; full credit at 3 |
| Relevant recruiter identified | 15 | ≥ 1 recruiter at the company with `role_relevance ≥ 50` |
| Recent career-page activity | 10 | A new posting appeared within 72h |
| High role relevance | 5 | Best matching role scores ≥ 80 |

### Worked example

```
Demo Consumer App — Hiring Activity 93/100
  ✓ 2 new relevant opening(s) discovered in the last 72h
  ✓ Relevant role posted 20 hours ago
  ✓ 2 relevant openings are live at once
  ✓ Relevant recruiter identified: Demo Recruiter Two
  ✓ Career page produced a new posting 18 hours ago
  ✓ Best matching role scores 97/100 for your profile
```

---

## 3. Recruiter relevance (0–100)

| Component | Max | How it is earned |
| --- | --- | --- |
| Company match | 25 | Works at the company you are tracking (40% if listed under a different entity) |
| Role / function match | 25 | Talent function (60 pts) + function alignment with your target roles (30) + seniority to own a requisition (10), scaled to 25 |
| Hiring activity | 20 | The company's hiring activity score |
| Email confidence | 15 | Provenance-weighted: `HIGH` 100%, `MEDIUM` 65%, `LOW` (inferred) 30%; +15% if verified valid, ×0.2 if invalid, ×0.6 if risky |
| Professional relevance | 10 | Public profile available, contact traced to a public source, associated with a target role |
| Recency | 5 | Full credit ≤ 7 days since last confirmed, decaying to zero at 120 days |

A recruiter marked `DO_NOT_CONTACT` is **excluded** (score 0, stated reason),
not merely ranked low.

### Worked example

```
Priya Sharma — Talent Acquisition
Recruiter Match: 94/100     Email Confidence: 97/100
  ✓ Works at Razorpay, a company you are tracking
  ✓ Works in a talent/hiring function (talent acquisition)
  ✓ Focused on product hiring, which covers your target roles
  ✓ Product Analyst posted 14 hours ago
  ✓ Professional email is published and attributed to this person
  ✓ Address passed verification
  ✓ Contact details last confirmed 6 hours ago
```

---

## 4. Outreach priority (0–100)

The final ranking on the dashboard, computed per (job, recruiter) pair.

| Component | Max |
| --- | --- |
| Job relevance | 30 |
| Hiring freshness | 20 |
| Recruiter relevance | 20 |
| Company priority | 15 |
| Email confidence | 10 |
| Recency | 5 |

Company priority contributes via a multiplier: CRITICAL 1.0, HIGH 0.8,
MEDIUM 0.55, LOW 0.3.

### Bands

| Score | Band |
| --- | --- |
| ≥ 90 | **CONTACT NOW** |
| ≥ 85 | HIGH PRIORITY |
| ≥ 70 | GOOD OPPORTUNITY |
| ≥ 60 | REVIEW |
| < 60 | LOW PRIORITY |

---

## The "contact today" engine

`services/scoring/outreach_priority.py :: contact_today()`:

1. Load active companies for the user (optionally excluding demo data).
2. Load open jobs at or above `contact_today_min_job_relevance` (default 50).
3. Load recruiters — **`do_not_contact` filtered in the SQL query itself**.
4. Drop pairs whose lead is already `CONTACTED`, `REPLIED`, `ARCHIVED` or
   `DO_NOT_CONTACT`, so nobody is ever recommended twice for the same role.
5. Score each remaining pair; drop anything below
   `contact_today_min_priority` (default 40).
6. Sort by priority, then by job relevance.
7. Cap each recruiter at two rows, so one person cannot fill the shortlist.

---

## Configurable options

Beyond the weights, these thresholds are editable on the Settings page:

| Option | Default | Effect |
| --- | --- | --- |
| `relevance_threshold` | 60 | A job at or above this counts as "relevant" |
| `fresh_job_window_hours` | 24 | Full freshness credit inside this window |
| `new_job_window_hours` | 72 | How long a discovery counts as a new signal |
| `freshness_full_hours` / `freshness_zero_hours` | 24 / 720 | The job freshness decay curve |
| `multiple_openings_target` | 3 | Openings needed for full "multiple" credit |
| `recruiter_recency_full_days` / `_zero_days` | 7 / 120 | Recruiter recency decay |
| `contact_today_min_job_relevance` | 50 | Floor for entering the shortlist |
| `contact_today_min_priority` | 40 | Floor for appearing on the dashboard |

After changing weights or taxonomy, click **Re-score all jobs** (or
`POST /api/jobs/rescore`) to apply them to existing records.

## The taxonomy

`taxonomy_terms` rows drive all matching. Kinds: `ROLE`, `INDUSTRY`, `SKILL`,
`LOCATION`, `SENIORITY`, `EXCLUDE`. Each term has aliases and a 0–1 weight, so
`Product Manager` can count for 0.7 while `Product Analyst` counts for 1.0.

Defaults seeded for a new account cover the target profile from the brief —
Associate Product Manager, Product Analyst, Product Operations, Business
Analyst, Growth Analyst, AI Product, Data Analyst, Product Strategy across
Fintech, Consumer Technology, SaaS and AI Products — and all of it is editable
from the UI. Entries in `user_profile` (target roles, industries, skills,
locations) are merged in on top, so a user who never opens Settings still gets
sensible results.
