# ⚾ MLB K Prop Analyzer

Automatically analyzes MLB pitcher strikeout props daily by cross-referencing:
- Team strikeout vulnerability (K rank, batting avg rank, batted balls rank)
- Starting pitcher K/9 rates
- Live prop bet lines from major sportsbooks (via The Odds API)

Runs every day at 11 AM ET via GitHub Actions. Publishes a web dashboard and sends a daily email.

---

## Setup Instructions

### Step 1 — Create a GitHub account
Go to [github.com](https://github.com) and sign up for a free account if you don't have one.

### Step 2 — Create a new repository
1. Click the **+** icon → **New repository**
2. Name it something like `mlb-k-analyzer`
3. Set it to **Public** (required for free GitHub Pages hosting)
4. Click **Create repository**

### Step 3 — Upload these files
Upload all files from this folder into your new repository:
- `mlb_analyzer.py`
- `requirements.txt`
- `.github/workflows/daily.yml`
- `README.md`

You can drag and drop them into the GitHub web interface.

### Step 4 — Enable GitHub Pages
1. Go to your repo → **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Select branch: `gh-pages`, folder: `/ (root)`
4. Click **Save**

Your dashboard will be live at: `https://YOUR_GITHUB_USERNAME.github.io/mlb-k-analyzer/`

### Step 5 — Add your secret API keys
1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each of the following:

| Secret Name | Value |
|---|---|
| `ODDS_API_KEY` | Your Odds API key |
| `SENDGRID_API_KEY` | Your SendGrid API key (see below) |
| `EMAIL_TO` | The email address to send the daily report to |
| `EMAIL_FROM` | The email address to send FROM (must be verified in SendGrid) |

### Step 6 — Set up free email sending (SendGrid)
1. Go to [sendgrid.com](https://sendgrid.com) and sign up for a free account
2. Free tier allows 100 emails/day — more than enough
3. Go to **Settings** → **API Keys** → **Create API Key**
4. Give it **Full Access**, copy the key
5. Go to **Settings** → **Sender Authentication** → verify the email address you want to send FROM
6. Add both to your GitHub secrets (step 5)

### Step 7 — Test it manually
1. Go to your repo → **Actions** tab
2. Click **Daily MLB K Prop Analyzer**
3. Click **Run workflow** → **Run workflow**
4. Watch it run — takes about 1-2 minutes
5. Check your email and your GitHub Pages URL

### Done!
The workflow runs automatically every day at 11 AM ET. You can also trigger it manually anytime from the Actions tab.

---

## How it works

**Composite Vulnerability Score:**
Each opposing team is ranked on 3 axes (lower rank = worse hitter = better target for Ks):
1. Most strikeouts taken (K Rank)
2. Lowest batting average (Avg Rank)  
3. Fewest batted balls (BBE Rank)

The composite score adds all 3 ranks together. Teams with the lowest composite score are the most vulnerable to strikeouts.

**Expected Ks:**
`Expected Ks = (Pitcher K/9 ÷ 9) × 5.5 innings × vulnerability adjustment`

**Edge Detection:**
- **OVER edge**: Expected Ks ≥ 0.5 above the prop line
- **UNDER edge**: Expected Ks ≤ 0.5 below the prop line

---

## Data Sources
- **MLB Stats API** — team hitting/pitching stats, probable pitchers (free, unofficial)
- **The Odds API** — pitcher strikeout prop lines from major books (free tier: 500 req/month)
- **SendGrid** — email delivery (free tier: 100 emails/day)
- **GitHub Actions** — daily automation (free for public repos)
- **GitHub Pages** — web dashboard hosting (free)

Total cost: **$0/month**
