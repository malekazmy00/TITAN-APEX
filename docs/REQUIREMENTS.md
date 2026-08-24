# REQUIREMENTS — TITAN-APEX OSINT / Scraping Platform

> هذا الملف هو المرجع الرسمي لقرارات الـ structure والمعايير في هذا المشروع.
> أي عمل جديد (كود، PR، إضافة provider أو target) لازم يلتزم بما هو مكتوب هنا.
> مصدر هذا الملف: خطة بناء المنصة (PROJECT_PLAN.md) المقدَّمة من صاحب المشروع.

---

## 0. البنية التحتية للتشغيل

| المكوّن | مكانه | السبب |
|---|---|---|
| **VPS** (Hetzner CX22 أو مماثل) | نواة المنصة (Scrapy, Playwright, Byparr, Redis, Queue) | إنترنت حقيقي بدون قيود شبكة، شغال 24/7 |
| **اللاب** (RTX 4070S) | طبقة تحليل الذكاء الاصطناعي (المرحلة 5، Qwen 14B) بس | فيه GPU |
| **Claude Code + Remote Control** | يشتغل جوه الـ VPS عبر SSH | تحكم عن بُعد |

---

## 1. هيكل الـ Repository (Interfaces + Config-driven)

```
titan-apex/
├── .github/workflows/
│   ├── ci.yml
│   └── lint.yml
├── src/
│   ├── core/
│   │   ├── interfaces/              # عقود مجردة (ABCs) - القلب اللي يخلي التوسع ممكن
│   │   │   ├── antibot_provider.py  # ABC: solve(url) -> Solution
│   │   │   ├── storage_backend.py   # ABC: save(item), query(filters), close()
│   │   │   └── ai_analyzer.py       # ABC: analyze(text) -> AnalysisResult
│   │   └── exceptions.py            # استثناءات مخصصة بدل Exception عامة
│   ├── providers/
│   │   ├── antibot/                 # implementation لاحق من antibot_provider (Phase 3)
│   │   └── storage/
│   │       └── sqlite_backend.py    # implementation أول من storage_backend (Phase 1)
│   ├── spiders/
│   │   ├── configs/                 # ملف YAML لكل موقع - مش كود جديد لكل هدف
│   │   │   └── quotes_toscrape.yaml
│   │   └── generic_spider.py        # spider واحد بيقرأ أي config
│   ├── middlewares/
│   │   ├── retry_backoff.py         # Phase 1
│   │   └── circuit_breaker.py       # Phase 2
│   ├── queue/                       # Celery/RQ task definitions (Phase 4)
│   ├── ai_analysis/                 # implementation فعلي لـ ai_analyzer (Phase 5)
│   ├── logging_config.py            # structured JSON logging
│   └── settings.py                  # كل الإعدادات من .env، صفر hardcoding
├── tests/
│   ├── unit/                        # كل ملف src له unit test مقابل - إلزامي
│   ├── integration/                 # اختبار تكامل بين المكونات
│   ├── contract/                    # كل provider جديد لازم يعدي نفس عقد الـ interface
│   └── fixtures/targets/            # snapshots ثابتة من مواقع تدريبية رسمية
├── docs/
│   ├── ARCHITECTURE.md
│   ├── REQUIREMENTS.md              # هذا الملف
│   ├── ADDING_A_TARGET.md
│   └── DEPLOYMENT.md
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── README.md
```

**مبدأ التوسع الأساسي:**
- إضافة موقع جديد = ملف `configs/*.yaml` جديد، **مش** كود جديد.
- تغيير خدمة antibot = implementation جديد لنفس الـ `interface`، **مش** تعديل الكود اللي بيستخدمه.
- أي provider جديد (antibot/storage/ai) لازم يعدي `tests/contract/` تلقائيًا قبل ما يُقبل.

---

## 2. مراحل البناء

### المرحلة 1 — الأساسيات (منفذة)
- [x] `pyproject.toml` + هيكل المجلدات كامل + `interfaces/` الثلاثة
- [x] `generic_spider.py` يشتغل بـ config واحد على موقع حقيقي (quotes.toscrape.com)
- [x] Retry + Exponential Backoff (middleware)
- [x] Structured logging (JSON)
- [x] Storage backend أول (SQLite)

### المرحلة 2 — المحتوى الديناميكي (منفذة)
- [x] Playwright كـ downloader middleware (`render_js: true` في config الـ target)
- [x] Circuit Breaker (قابل للتهيئة، افتراضي 5 فشل متتالي / 60 ثانية cooldown)
- [x] Rate limiting ذاتي عبر config كل target (`rate_limit`, `max_concurrency`)

### المرحلة 3 — الحماية المتوسطة (منفذة)
- [x] `byparr_provider.py` implementation من `antibot_provider` (عدّى `tests/contract/`)
- [x] Cookie management تلقائي (عبر `Set-Cookie` headers + Scrapy's `CookiesMiddleware` الموجود أصلاً)
- [x] Fallback مسجل بوضوح (مش كراش) لو الـ provider فشل، أو لو `TITAN_BYPARR_URL` مش متظبط أصلاً

### المرحلة 4 — التنظيم والتوسع (منفذة)
- [x] Redis + RQ (`src/queue/`: `connection.py`, `tasks.py`, `enqueue.py`) —
      كل job بيشتغل في subprocess منفصل (Twisted reactor ميتفتحش غير مرة
      واحدة لكل process)
- [x] Logging + Alerting بسيط (فشل متكرر = تنبيه): `src/alerting.py`،
      متوصل بـ `CircuitBreakerMiddleware` — لما circuit يفتح (5 فشل
      متتالي افتراضيًا) بيتسجل CRITICAL دايمًا، وبيتبعت webhook لو
      `TITAN_ALERT_WEBHOOK_URL` متظبط

### المرحلة 5 — طبقة الذكاء الاصطناعي (على اللاب) (الكود منفذ، الـ inference الحي pending)
- [x] `ai_analyzer` implementation بـ Qwen عبر Ollama —
      `src/ai_analysis/ollama_analyzer.py` (`OllamaAnalyzer`)، الموديل
      الافتراضي **`qwen3:14b`** (عام، مش `qwen2.5-coder` المتخصص في كود —
      المهمة هنا تلخيص/تصنيف نص OSINT مش كود)
- [x] `analyze(text) -> AnalysisResult`: structured output مضمون عبر
      `format` (JSON schema) في Ollama — مش نص حر، JSON دايمًا
- [x] Contract tests (`tests/contract/test_ai_analyzer_contract.py`)
- [x] Config قابل للتهيئة: `TITAN_OLLAMA_URL`, `TITAN_AI_MODEL`
      (`.env.example`)
- [ ] **الـ inference الحي الفعلي على GPU حقيقي — pending، مسجل في
      القسم 5 تحت**

### المرحلة 6 — Proxies (مؤجلة)
- implementation جديد فوق نفس `antibot_provider` interface، بس عند الحاجة الفعلية

---

## 3. أوامر الاختبار الصارمة (إلزامية - لا استثناء)

### قبل أي commit:
```bash
ruff check src/ tests/ --fix
mypy src/ --strict
pytest tests/unit -v --cov=src --cov-fail-under=85
pytest tests/contract -v
```

### قبل أي دمج على `main` (CI):
```bash
pytest tests/unit tests/integration tests/contract -v
```

### قاعدة "No Silent Failure":
كل test في `tests/unit` لازم يغطي:
1. الحالة الناجحة (happy path)
2. على الأقل حالتين فشل (خطأ اتصال، رد غير متوقع/فاسد)
3. التأكد إن الموارد (اتصال، ملف) بتتقفل حتى في حالة الفشل (`finally` أو context manager)

أي كود فيه `except Exception` عام أو `except: pass` بدون تسجيل واضح للسبب — **يُرفض تلقائيًا**.

### قاعدة "لا افتراض قيد بيئة":
أي فشل بسبب افتراض قيد شبكة/بيئة لازم يتأكد فعليًا في GitHub Actions
(`services:` أو step مباشر) قبل ما يُسجّل في Pending Real-Network
Verification — مش يُفترض تلقائيًا إنه pending.

---

## 4. Definition of Done

- ✅ Unit + contract tests عدّت محليًا وفي CI
- ✅ Coverage ≥ 85%
- ✅ `mypy --strict` بدون أخطاء
- ✅ صفر `except` عام غير محدد
- ✅ كل موارد خارجية مقفولة (`finally` أو context manager)
- ✅ إعدادات جديدة عبر `.env.example` مش hardcoded
- ✅ لو provider جديد: عدّى `tests/contract/` بالكامل
- ✅ لو target جديد: أضيف كـ `config.yaml` بس، صفر كود مكرر

---

## 5. Pending Real-Network Verification

> بنود بتتسجل هنا لما فشل إثباتها في بيئة التطوير (sandbox) يفضل غامض —
> هل هو قيد بيئة حقيقي (محتاج VPS) ولا حاجة تانية؟ لا تتشال من هنا إلا
> بعد إثبات حاسم في بيئة بإنترنت حر فعلي (GitHub Actions runners، أو الـ
> VPS بعدين). دلوقتي مفيش بنود pending.

### بنود Pending حاليًا

| البند | الحالة | تاريخ التسجيل |
|---|---|---|
| **تحليل AI الحي** (`OllamaAnalyzer.analyze()` ضد موديل حقيقي، `qwen3:14b`) | الكود اتبنى واتاختبر بالكامل (unit tests + contract tests بحقن HTTP client وهمي، زي `retry_backoff`/`byparr_provider` قبل كده) + دليل حي جزئي: HTTP transport الحقيقي (uninjected) اتجرب فعليًا ضد سيرفر HTTP محلي حقيقي بيتكلم نفس بروتوكول `/api/generate` (مش mock داخل الـ process) — الطلب اتبنى صح، الـ `format` (JSON schema) اتبعت صح، والرد اتحلل صح. **الـ inference الحقيقي على GPU (استدعاء `qwen3:14b` فعليًا عبر Ollama حقيقي) لسه محتاج إثبات على اللاب (RTX 4070S)** — ده **استثناء شرعي** من قاعدة "جرب في GitHub Actions الأول": ده قيد هاردوير حقيقي (GPU) مش افتراض قيد شبكة/بيئة — لا بيئة التطوير دي ولا GitHub Actions runners العادية عندهم GPU، فمفيش طريقة تانية تثبته غير على اللاب فعليًا. | 2026-08-21 |

**الخطوة المطلوبة لقفل البند ده:** بعد ما اللاب يبقى جاهز (القسم 0)،
شغّل Ollama حقيقي (`ollama serve`, `ollama pull qwen3:14b`)، وشغّل:
```python
from src.ai_analysis.ollama_analyzer import analyzer_from_env
analyzer = analyzer_from_env()
result = analyzer.analyze("<نص حقيقي من scraping>")
print(result)
```
وتأكد إن `result.summary`/`result.entities` منطقيين فعليًا (مش بس JSON
صحيح شكليًا) — يعني تحقق من جودة المحتوى، مش بس إن الـ schema اتاحترم.

### بنود اتحلت

الاتنين اللي كانوا مسجلين قبل كده (2026-08-21) اتحلوا نهائيًا في نفس اليوم:

- **رندر Playwright الحي**: كان مسجل "pending" على أساس إنه قيد شبكة في
  sandbox التطوير. لما اتشغّل فعليًا جوه GitHub Actions (إنترنت حر
  بالكامل) عبر `tests/integration/test_playwright_live_render.py`، طلع
  إن السبب **مكنش قيد بيئة أصلاً** — كان **باج حقيقي في الكود**:
  `render_with_playwright()` كان بيعمل `page.goto()` بس من غير أي
  scroll، فصفحات الـ infinite-scroll كانت بترجع نفس الـ 12 عنصر
  الموجودين في الـ HTML الثابت بس (CI run
  [32436779117](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32436779117)
  فشل بـ `assert 12 > 12`، دليل واضح إن المتصفح وصل وقرأ الصفحة صح، بس
  من غير scroll). اتصلح بإضافة scroll-to-bottom loop قبل قراءة الـ
  content (`src/middlewares/playwright_middleware.py`,
  `_scroll_to_load_lazy_content`)، وأعيد التشغيل ونجح فعليًا: CI run
  [32437190471](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32437190471) —
  `test_infinite_scrolling_target_yields_more_than_the_static_batch PASSED`.
- **Byparr الحقيقي جوه Docker**: نفس الـ CI run
  [32437190471](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32437190471)
  شغّل الـ Byparr container الحقيقي (`ghcr.io/thephaseless/byparr:latest`
  كـ `services:` في `ci.yml`) وحلّ تحدي anti-bot حقيقي على
  `scrapingcourse.com/antibot-challenge` — لوج الـ container نفسه بيقول:
  `Challenge detected, waiting for it to clear... Clicked the challenge
  checkbox (attempt 1/2)... Done ... in 13.18s`. الاختبار
  `tests/integration/test_byparr_live_solve.py` عدّى: `PASSED`.

كلا الاختبارين بقوا جزء دائم من `ci.yml` (job-level `byparr` service +
خطوة "Integration tests") — بيتشغّلوا فعليًا على كل push/PR، مش
موثقين كـ "هيتحقق منهم بعدين" بس.

---

## 6. تغطية Test Targets (docs/TEST_TARGETS.md)

توسيع منظّم لعدد الـ targets الحقيقية اللي `generic_spider.py` بيغطيها،
حسب قائمة `docs/TEST_TARGETS.md` (19 موقع رسمي، 5 مستويات صعوبة). كل
target جديد = `configs/*.yaml` جديد + integration test حي واحد في
`tests/integration/` بنفس أسلوب `antibot-challenge`/`cloudflare-challenge`
— **صفر كود جديد في `src/`**.

### Configs جديدة (المستوى 1-3)

| Config | المستوى | ملاحظة اكتشاف مهمة |
|---|---|---|
| `books_toscrape.yaml` | 1 | العنوان الظاهر مقصوص بـ "..."، لازم `h3 a::attr(title)` مش النص الظاهر |
| `scrapethissite_simple.yaml` | 1 | كل الـ 250 دولة في صفحة واحدة، بدون pagination |
| `scrapethissite_forms.yaml` | 1 | **باج حقيقي في الموقع نفسه** (مش عندنا): رابط "Next" في الصفحة من غير `?page_num` بيرجع لصفحة 1 نفسها بدل ما يتقدّم — الحل بـ config بس: start URL صريح `?page_num=1` |
| `scrapethissite_frames.yaml` | 2 | صفحة الـ wrapper فاضية (iframe shell بس) — `GenericSpider` مبينفذش JS ولا بيتبع iframe src؛ الحل: start_url يشاور مباشرة على مستند الـ iframe نفسه (`?frame=i`) |
| `quotes_toscrape_js.yaml` | 2 | نفس بيانات quotes.toscrape.com لكن مُرندرة كاملة بـ JS من array مضمّن؛ نفس الـ selectors ونفس نمط pagination (`li.next a`) بالظبط، `render_js: true` بس اللي اتغير |
| `quotes_toscrape_scroll.yaml` | 2 | infinite scroll حقيقي (`div.quotes` فاضية في الـ HTML الثابت)، نفس نمط `scrapingcourse_infinite_scrolling.yaml` |
| `scrapingcourse_javascript_rendering.yaml` | 3 | الـ HTML الثابت فيه 13 placeholder فاضي (name/price موجودين بس من غير نص) — `render_js: true` إلزامي |
| `scrapingcourse_pagination.yaml` | 3 | ثابت بالكامل، 13 صفحة، `a.next-page` / `rel="next"` واضح |
| `webscraper_io_pagination.yaml` | 3 | اخترنا صفحة `pagination` (كتالوج سيارات) كتمثيل ملموس من كتالوج `webscraper.io/test-sites` المذكور بشكل عام في القائمة؛ حقل `details` (سنة/بلد/مسافة) بيرجع list واحد مش 3 حقول منفصلة لأن الثلاثة بياخدوا نفس الـ class بالظبط بدون أي تمييز — قيد في المعمارية الحالية (كل field له selector واحد جوه الـ item)، مش باج |
| `webscraper_io_scroll.yaml` | Tier 2 | infinite scroll حقيقي (`data-next-page` marker)، نفس نمط `scrapingcourse_infinite_scrolling.yaml` — نجح فعليًا في CI |
| `scrapethissite_ajax_javascript.yaml` | 2 | ✅ **اتحل (2026-08-21)**: `render_wait_ms: 2500` — تفاصيل تحت وفي القسم 7 بند 3 |
| `webscraper_io_load_more.yaml` | Tier 2 | ✅ **اتحل (2026-08-21)**: `click_selector: "button.load-more-btn"` — تفاصيل في القسم 7 بند 4 |

> **تصحيح (2026-08-21):** `scrapethissite_ajax_javascript.yaml` كان
> متسجّل هنا قبل كده كـ "نجح" بناءً على أول تشغيلة حقيقية له في CI (run
> [32443029436](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32443029436)).
> تشغيلة تانية حقيقية بعد كده (run
> [32471707326](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32471707326))
> فشلت بـ `assert 0 > 0` — نفس الـ config، نفس التارجت، نتيجة مختلفة.
> يعني الفجوة الزمنية اللي كانت متوقّعة (تأخير الموقع ~1.5 ثانية بعد رد
> الـ AJAX، أطول من انتظار الـ scroll-loop) حقيقية فعلًا ومش دايمًا
> بتتغطى بالصدفة — مش باج ثابت نقدر نصلّحه بإعادة المحاولة. الـ
> config والاختبار اتشالوا نهائيًا (مش هيفضلوا موجودين كـ "شبه شغالين")،
> واتسجّل رسميًا كـ Known Spider Limitation في القسم 7 تحت.
>
> **تحديث لاحق:** الفجوة دي اتقفلت فعليًا بعد كده بإضافة `render_wait_ms`
> — راجع القسم 7 بند 3 للتفاصيل والدليل الحي (run 32473641117).

### فجوات مُكتشفة — مش هتتغطى بـ "صفر كود جديد"

`quotes.toscrape.com/login` و`quotes.toscrape.com/tableful` اتسجّلوا
رسميًا كـ **"Known Spider Limitations"** في القسم 7 تحت — مش
"Pending Real-Network Verification": المشكلة هنا مالهاش علاقة بإثبات
حي في بيئة بإنترنت حر، هي قيد معماري حقيقي في `GenericSpider` نفسه
هيفضل موجود لحد ما حد يقرر يضيف كود جديد.

(`scrapethissite.com/pages/ajax-javascript` و
`webscraper.io/test-sites/load-more` كانوا مسجّلين هنا كـ Known Spider
Limitations كمان، بس اتحلّوا فعليًا بعد كده بـ `render_wait_ms`/
`click_selector` — التفاصيل والدليل الحي في القسم 7، بنود 3 و4.)

### المستوى 4 — `scrapingclub.com`: النتيجة نتيجة موقع، مش كود

عند الفحص الفعلي (2026-08-21)، دومين `scrapingclub.com` بالكامل بقى
بيرجّع موقع كازينو غير متعلق ("Winspirit Casino Australia") — `curl` على
الصفحة الرئيسية والمسارات المعروفة من الموقع الأصلي
(`/exercise/list_basic/`) بترجع 404 على المسار القديم و200 على صفحة
الكازينو. يعني الموقع اتغيّر ملكيته/محتواه من وقت ما القائمة اتعملت. **مفيش
config اتعمل لـ scrapingclub.com، ومفيش أي طلب scraping اتبعت غير طلبين
تحقّق (GET صفحة رئيسية + مسار تمرين قديم)** — مش target تدريبي شرعي
دلوقتي، والفشل ده مالوش علاقة بـ Byparr ولا `CircuitBreakerMiddleware`.
يُنصح بشطبه من `TEST_TARGETS.md` أو استبداله بموقع بديل لاحقًا.

### المستوى 5 — `hackernews.yaml`

بيانات متغيّرة فعليًا (مش ثابتة زي كل target سابق). كل story منتشر على
صفين `<tr>` منفصلين (زي `/tableful`) — الـ config بياخد بس الحقول
اللي في `tr.athing` نفسها (rank/title/url/site)؛ النقاط وعدد التعليقات
موجودين في صف تاني منفصل، فمش متاحين بدون نفس التوسعة المعمارية المذكورة
فوق. اختبار `tests/integration/test_hackernews_live.py` بيشغّل الكراول
مرتين بفاصل زمني حقيقي (45 ثانية) ضد الموقع الحي، ويقارن النتائج —
النتيجة الفعلية (هل الترتيب/العناوين اتغيّرت ولا لأ) موثّقة في تقرير Test
Targets النهائي بدل ما تتفترض.

### Tier 2 List A — anti-bot تاني + أدوات تشخيص

كله اتّحقّق منه فعليًا في CI run
[32471707326](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32471707326):

- **`nowsecure.nl`**: `ByparrProvider.solve()` نجح (status 200، html
  طويل). ملحوظة مهمة: على عكس `cloudflare-challenge` (block على مستوى
  WAF)، تحدي `nowsecure.nl` client-side/cosmetic بس — `curl` عادي بيرجع
  200 بالصفحة كاملة من غير أي حل، والـ Turnstile widget بيستخدم
  sitekey الاختباري الرسمي من Cloudflare نفسه (`3x00000000000000000000FF`،
  بينجح دايمًا). يعني النجاح هنا بيثبت إن Byparr's browser بيشغّل
  الصفحة وJS بتاع Turnstile من غير error، مش إنه بيتخطى قيد وصول حقيقي.
- **`bot.sannysoft.com`**: مش target بيانات — خطوة تشخيص بعد أي تحسين
  في `byparr_provider`/stealth args، بالظبط زي ما اتفق عليه. الاختبار
  (`tests/integration/test_sannysoft_diagnostic_live.py`) بيحل الصفحة
  عبر Byparr، يقرأ كل خلية `id="*-result"` (class="passed"/"failed" —
  الموقع نفسه بيحطها بـ JS)، ويطبعها كدليل في لوج الـ CI. الاختبار
  **مش** بيفشّل بناءً على نتيجة تشخيصية معينة (مهمة Byparr هي حل
  anti-bot، مش إخفاء كل علامات الأتمتة بالكامل).
- **`browserleaks.com`**, **`demo.fingerprint.com/web-scraping`**: متسجّلين
  في `docs/TEST_TARGETS.md` بس لسه مش مُدمجين — نفس فكرة sannysoft،
  هيتضافوا بعدين لو احتجناهم (مش أولوية دلوقتي).

### webscraper.io/test-sites — الكتالوج الحقيقي أصغر من المتوقع

عند الفحص الفعلي، الكتالوج الحي دلوقتي 4 صفحات بس: `pagination`
(مُختبر ✅)، `scroll` (مُختبر ✅ — `webscraper_io_scroll.yaml`)،
`load-more` (فجوة حقيقية، القسم 7 بند 4)، و
`website-state-setup-login` (نفس قيد login، القسم 7 بند 1). مفيش
صفحات "dropdown filters"/"nested categories" منفصلة موجودة على الموقع
دلوقتي.

---

## 7. Known Spider Limitations

> **الفرق عن القسم 5 (Pending Real-Network Verification):** بند في
> القسم 5 معناه "الكود جاهز ومُختبر، وبس محتاج إثبات حي في بيئة
> معينة" — بيتشال لما الإثبات ده يحصل. بند هنا معناه حاجة تانية
> تمامًا: **قيد معماري حقيقي وثابت في `GenericSpider`/`SpiderConfig`
> النهارده** — مش هيتحل بإثبات حي ولا بانتظار بيئة معينة، هيفضل موجود
> لحد ما حد يقرر يكتب كود جديد يعالجه فعليًا. مفيش target هنا
> "هيشتغل بعدين لوحده".
>
> **تحديث (2026-08-21):** بندين من الخمسة (3 و4) اتقفلوا فعليًا بعد ما
> الكود الجديد اللي كانوا مستنيينه اتكتب وتحقّق منه حي في CI —
> `render_wait_ms`/`click_selector` على `SpiderConfig`. باقيين هنا
> كسجل تاريخي (يوضحوا ليه القيد كان موجود وإزاي اتحل)، مش لأنهم لسه
> مفتوحين. البنود 1، 2، و5 لسه مفتوحة فعليًا.

### 1. `quotes.toscrape.com/login` (و`webscraper.io/test-sites/website-state-setup-login` بنفس القيد بالظبط) — POST / forms / session

**الفجوة:** الصفحتين محتاجين تسجيل دخول فعلي — `quotes.toscrape.com`
عبر `POST /login` مع `csrf_token` (hidden input بيتغيّر كل مرة) +
`username` + `password`؛ `webscraper.io`'s login page نفس الفكرة (form
فيه username/password، على الأرجح AJAX-based مش POST تقليدي) — وبعدين
إدارة session/cookies عبر باقي الطلبات عشان الصفحة تفضل شايفة المستخدم
مسجّل دخول.

**ليه `GenericSpider` النهارده مش قادر:** الـ spider بيعمل `GET` بس
على `start_urls` (`async def start()` / `start_requests()` كلاهم
بيبنوا `scrapy.Request` عادي، من غير أي مفهوم لـ form/POST). مفيش أي
مسار لـ:
- استخراج قيمة hidden field (زي `csrf_token`) من صفحة قبل ما تبعتها
  في طلب تاني
- بناء `FormRequest` (POST) بدل `Request` (GET)
- التفريق بين "طلب أول لازم يجيب توكن" و"طلب تاني بيستخدم التوكن ده"

**لو قررنا نضيفها بعدين:** محتاجة كود جديد حقيقي في
`generic_spider.py` (أو middleware مخصص) — مش `config.yaml` بس. أقل
حاجة لازم تتضاف:
- حقل config اختياري زي `login: {url, method: POST, form_fields,
  csrf_selector}`
- منطق جديد في الـ spider يعمل GET أول لصفحة اللوجن، يستخرج التوكن،
  يبني `FormRequest` بيه، ويتأكد إن الـ session (Scrapy's
  `CookiesMiddleware` الموجود أصلاً) بيفضل شغال في باقي الطلبات
- unit tests جديدة تغطي: نجاح اللوجن، توكن مفقود/غير متوقع، فشل
  الـ POST نفسه

### 2. `quotes.toscrape.com/tableful` (و`hackernews.yaml` بنفس القيد جزئيًا)

**الفجوة:** كل "item" منطقي منتشر على أكتر من عنصر HTML واحد، مش
عنصر واحد بكل حقوله جواه:
- `/tableful`: كل quote منتشر على صفين `<tr>` منفصلين (نص+مؤلف
  ملزوقين في text node واحد من غير أي فاصل/class في الصف الأول،
  والـ tags في صف تاني تمامًا)
- `hackernews.yaml`: كل story منتشر على صفين `<tr>` (`tr.athing`
  للعنوان/الرابط، وصف تاني منفصل للنقاط/عدد التعليقات) — هنا الـ
  config بياخد بس بيانات الصف الأول (rank/title/url/site)، فمش نفس
  درجة الفجوة (مفيش فشل، بس بيانات ناقصة موثّقة)

**ليه المعمارية الحالية مش قادرة:** `SelectorsConfig` (في
`src/spiders/spider_config.py`) بتفترض `item` = selector واحد، وكل
`field` = selector تاني *جوه* نفس العنصر ده (`row.css(field_selector)`
في `GenericSpider.parse()`). مفيش مفهوم لـ "اجمع الصف ده مع الصف اللي
بعده" ولا "قطّع النص ده على أساس نمط معين" (regex/split).

**لو قررنا نضيفها بعدين:** كود جديد حقيقي، مش config:
- تجميع صفوف متعددة: مفهوم جديد زي `item_group` (عدد الصفوف اللي
  بيتجمعوا كـ item واحد) أو selector لـ "sibling التاني"
- تقطيع نص: قدرة اختيارية لكل field تحدد regex أو separator يقطّع بيه
  نص واحد لأكتر من قيمة (زي فصل "quote text" عن "Author: X")
- unit tests جديدة تغطي: تجميع صفوف ناجح، عدد صفوف غير متوقع
  (missing sibling)، فشل الـ regex/split

### 3. `scrapethissite.com/pages/ajax-javascript` — انتظار ثابت مش كافي لتأخير متعمّد من الموقع — ✅ اتحل (2026-08-21)

**الفجوة:** الموقع بيضيف تأخير JS متعمد (`setTimeout` ~1.5 ثانية) بعد
ما رد الـ AJAX يوصل وقبل ما يعرض الصفوف فعليًا في الـ DOM (تعليق في
الكود المصدري للموقع نفسه بيقول "add intentional delay to emphasize
async UI"). `PlaywrightMiddleware`'s scroll-loop (`_scroll_to_load_lazy_content`)
بيدّي انتظار إضافي بس كـ **أثر جانبي** لمحاولة اكتشاف نمو الصفحة عبر
scroll — مش انتظار مضمون بمدة معينة. النتيجة: **فجوة زمنية حقيقية
موثّقة بدليلين حقيقيين من CI، مش افتراض**:
- run [32443029436](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32443029436): PASSED
- run [32471707326](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32471707326): FAILED (`assert 0 > 0`)

نفس الـ config، نفس التارجت، نتيجتين مختلفتين — يعني مش باج نقدر
نصلّحه بإعادة المحاولة لحد ما ينجح، ده سلوك غير موثوق فعليًا. الـ
config (`scrapethissite_ajax_javascript.yaml`) والاختبار المرتبط اتشالوا
من الـ repo نهائيًا (2026-08-21) بدل ما يفضلوا موجودين وهما مش موثوق
فيهم.

**ليه المعمارية الحالية مش قادرة:** مفيش حقل config زي `render_wait_ms`
أو أي طريقة تحدد "استنى مدة ثابتة كمان بعد الـ navigation" —
`_scroll_to_load_lazy_content` بياخد `max_attempts`/`pause_ms` كـ
باراميترز للدالة نفسها (مش من الـ config)، وبيوقف بدري لو ارتفاع
الصفحة مبقاش بيكبر (زي هنا: الصفحة مش infinite-scroll أصلاً، فبتوقف
من أول محاولة تقريبًا).

**لو قررنا نضيفها بعدين:** كود جديد في `PlaywrightMiddleware`/
`SpiderConfig`:
- حقل config اختياري زي `render_wait_ms` بيتضاف كانتظار إضافي ثابت
  بعد الـ navigation (وقبل الـ scroll-loop)، مستقل عن منطق الـ scroll
- unit test جديد يتأكد إن القيمة دي بتتوصّل فعليًا لـ `render_with_playwright`

**✅ تحديث (2026-08-21) — اتحل فعليًا:** الكود الجديد المذكور فوق
اتنفّذ بالظبط زي ما كان متوصّف — `render_wait_ms: int | None` على
`SpiderConfig`، بيوصل عبر `GenericSpider._request_meta()` لـ
`request.meta`، و`PlaywrightMiddleware`/`render_with_playwright` بيعمل
`page.wait_for_timeout(render_wait_ms)` بعد الـ scroll-loop وقبل قراءة
`page.content()`. `scrapethissite_ajax_javascript.yaml` رجع بـ
`render_wait_ms: 2500` (تقريبًا ضعف تأخير الموقع ~1.5 ثانية)، والاختبار
الحي رجع كمان. **النتيجة اتّحقّق منها فعليًا في CI، مش افتراض**: run
[32473641117](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32473641117)
— `test_scrapethissite_ajax_javascript_yields_rows_after_hash_triggered_fetch PASSED`.
الفجوة دي اتقفلت رسميًا.

### 4. `webscraper.io/test-sites/load-more` — زرار محتاج click، مش scroll — ✅ اتحل (2026-08-21)

**الفجوة:** المحتوى الإضافي محجوب وراء
`<button class="load-more-btn ecommerce-items-scroll-more">Load More</button>`
— اسم الـ class بيوحي إنه ممكن يكون scroll-wired، بس النتيجة الفعلية
في CI (run [32471707326](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32471707326))
كانت 6 عناصر بالظبط (نفس الدفعة الأولى الثابتة) — **مش أكتر**، يعني
الزرار محتاج click حقيقي مش scroll. النتيجة دي حاسمة ومتكررة (مش
flaky زي البند اللي فوق)، فمش هنعيد المحاولة أو نفترض حاجة تانية.
الـ config (`webscraper_io_load_more.yaml`) والاختبار المرتبط اتشالوا
من الـ repo نهائيًا (2026-08-21).

**ليه المعمارية الحالية مش قادرة:** `PlaywrightMiddleware` بيعمل
`goto()` + scroll-to-bottom loop بس — مفيش أي مفهوم لـ "دوّر على
عنصر واستخدم click عليه" (زي `page.click(selector)` في Playwright
نفسها).

**لو قررنا نضيفها بعدين:** كود جديد:
- حقل config اختياري زي `click_selector` بيخلي
  `render_with_playwright` يدور على العنصر ده بعد الـ navigation
  ويعمله click (مع انتظار قبل قراءة الـ content)، يتكرر لو محتاج (زي
  زرار "Load More" اللي ممكن يظهر أكتر من مرة)
- unit tests جديدة تغطي: click ناجح بيزود المحتوى، العنصر مش موجود
  (لا يبوّظ الكراول)، فشل الـ click نفسه

**✅ تحديث (2026-08-21) — اتحل فعليًا:** `click_selector: str | None`
اتضاف على `SpiderConfig`، نفس مسار `render_wait_ms` فوق (meta →
`PlaywrightMiddleware` → `render_with_playwright`)، وبيعمل
`page.click(click_selector, timeout=timeout_ms)` فورًا بعد `page.goto()`
(Playwright بيسكرول للعنصر لوحده قبل الـ click). `webscraper_io_load_more.yaml`
رجع بـ `click_selector: "button.load-more-btn"`، والاختبار الحي رجع
كمان. **النتيجة اتّحقّق منها فعليًا في CI، مش افتراض**: run
[32473641117](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32473641117)
— `test_webscraper_io_load_more_yields_more_than_the_first_batch PASSED`
(أكتر من 6 عناصر، تأكيد إن الزرار اتضغط فعليًا). الفجوة دي اتقفلت رسميًا.

### 5. SPA حقيقي (`react-shopping-cart`) — CSS-in-JS بـ hashed class names

**الفجوة:** بعد بحث فعلي (2026-08-21) عن SPA demo مفتوح المصدر مناسب
(المستخدم اقترح `react-shopping-cart` بالاسم)، اتفحص فعليًا:
`https://react-shopping-cart-67954.firebaseapp.com/` — شغّال (200،
demo حقيقي MIT من jeffersonRibeiro). الكود المصدري اتـ`clone` فعليًا
(`github.com/jeffersonRibeiro/react-shopping-cart`) للتأكد بدل التخمين:
كل "item" منطقي (منتج) بيتكوّن من عناصر شقيقة (siblings) —
`S.Image`/`S.Title`/`S.Price`/`S.BuyButton` — كلهم أبناء مباشرين لنفس
الـ `S.Container`، مفيهمش أي عنصر واحد "بيلف" حواليهم غير الـ Container
نفسه. المشكلة: `styled-components` بيولّد class names hashed وقت الـ
build (`sc-xxxxx`) — مفيش `data-testid` ولا class ثابت على الـ
Container يوصفه بشكل يمكن الاعتماد عليه CSS-selector-wise، ومفيش
`:has()` في `cssselect` (اللي Scrapy/parsel بيستخدموه) يسمح بـ "اختار
الأب اللي جواه عنصر معين".

**استثناء جزئي مثير للاهتمام:** `S.Image` بتتكتب كـ
`<S.Image alt={title} />` — و`styled-components` بينقل أي attribute
اسمه معروف كـ HTML attribute حقيقي (زي `alt`) للـ DOM حتى لو مش منطقي
للعنصر ده (`div`)، فعلى الأرجح `div[alt]::attr(alt)` كان هيرجّع
العنوان فعليًا. بس العنوان لوحده مش كافي (بدون سعر)، والسعر نفسه
sibling مش descendant من أي عنصر تاني موثوق.

**ليه المعمارية الحالية مش قادرة:** نفس القيد المعماري بتاع بند 2
(`tableful`/`hackernews`) — item واحد لازم يبقى عنصر HTML واحد بكل
حقوله كـ descendants جواه — بس هنا السبب الجذري مختلف: مش جدول قديم،
هو نمط CSS-in-JS شائع في تطبيقات React حديثة (styled-components،
emotion، إلخ) بيمنع أي اعتماد على class ثابت للـ container.

**لو قررنا نضيفها بعدين:** كود جديد، احتمالين:
- دعم `:has()`-style logic يدوي (مش عبر cssselect) لاختيار "أقرب أب
  لعنصر بعينه" — تعقيد إضافي حقيقي في `GenericSpider.parse()`
- أو دعم "field جواه sibling معيّن" بدل item واحد بكل حقوله جواه —
  نفس التوسعة المطلوبة أصلاً لبند 2

**بديل اتفحص وطلع ميت فعليًا:** RealWorld/Conduit demo family
(`react-redux.realworld.io`, `demo.realworld.io`) — الـ frontend
hosting بيرجّع 404 (S3 `NoSuchBucket`)، وحتى الـ frontend الشغّال
(`react-mobx.realworld.io`) بيتكلم مع backend API ميت
(`conduit.productionready.io` → Cloudflare 530، DNS error على الـ
origin). مفيش بيانات حقيقية تتسحب حتى لو حلّينا مشكلة الـ selectors.

---

## 8. Escalation Cycle

هذا القسم هو المرجع الرسمي للـ dorna (الدورة) اللي المشروع بيمشي بيها
من هنا فصاعدًا (2026-08-21). الهدف: منع أي وهم إن "الكود خلص" —
النجاح دايمًا نسبي لمستوى صعوبة بيئة اختبار معروف ومسجّل، مش نهائي.

### الدورة الرسمية

1. **تطوير/تحسين الكود** — إضافة provider جديد، حقل config جديد، إصلاح
   باج، إلخ. نفس القواعد الصارمة المعتادة في القسم 3 (coverage، صفر
   `except` عام، إلخ) بتنطبق زي العادة.
2. **اختبار تصاعدي كامل** — تشغيل **كل** الـ targets الحالية (كل
   `tests/integration/*_live.py` الموجودين، مش بس اللي اتغيّروا في
   الجولة دي) + أي إضافة جديدة لبيئة الاختبار، في CI حقيقي (مش محليًا).
3. **توثيق أي نجاح كامل بدون flaky/retraction** — لو target اتسجّل
   "نجح"، لازم يكون نجح فعليًا في نفس الجولة، مش نجح مرة وفشل مرة
   (نفس القاعدة اللي اكتشفت `scrapethissite_ajax_javascript` الأولى:
   نجاح واحد مش كافي، لازم يكون ثابت/مش flaky قبل ما يتسجّل كنجاح
   نهائي).
4. **تصعيد صعوبة بيئة الاختبار خطوة واحدة** — طبقة أمنية جديدة
   (anti-bot أصعب) أو تحدي هيكلي جديد (نمط JS/DOM أعقد)، واحد بس في
   كل جولة، مش أكتر — عشان أي فشل يبقى واضح السبب.
5. **اختبار تاني** — رجوع لخطوة 2 على المستوى الجديد.
6. **تكرار** — الدورة كلها بترجع لخطوة 1 لو الجولة كشفت باج/فجوة
   محتاجة كود جديد (زي `render_wait_ms`/`click_selector`)، أو تتقدّم
   مباشرة لخطوة 4 (تصعيد تاني) لو كل حاجة نجحت بدون تعديل.

### المبدأ الصريح (لازم يتقرا قبل أي إعلان "نجاح")

> "كل الاختبارات نجحت" في أي جولة تعني الكود قوي بما يكفي **للمستوى
> الحالي من الصعوبة بس** - مش دليل إن الكود جاهز للواقع، ومش وصلنا
> للحد الأقصى. السقف الحقيقي يفضل مجهول طالما بيئة الاختبار لسه
> بتتطور.

يعني عمليًا: أي تقرير "كل الـ integration tests خضرا" لازم يترفق
بذكر مستوى الصعوبة الحالي (نفس ترقيم `docs/TEST_TARGETS.md` +
`test-environment/CHANGELOG.md`)، مش يتقرا كـ "المشروع خلص" أو
"الكود production-ready".

### سجل التصعيد — `test-environment/CHANGELOG.md`

كل جولة تصعيد (خطوة 4 فوق) تتسجّل هناك، سطر واحد بالشكل الثابت ده:

```
[التاريخ] من مستوى X لمستوى X+1 - أضفنا كذا - نتيجة الكود (نجح كامل/فشل جزئي/فشل كامل) - القرار (نصلح دلوقتي/نأجل ونسجل/نقبله كحد)
```

القيم المسموحة لعمودي "النتيجة"/"القرار" ثابتة (زي ما هي فوق حرفيًا)
عشان السجل يفضل قابل للمسح بسرعة عبر الزمن. تفاصيل أي فجوة مكتشفة
(السبب، الحل المحتمل) تتوثّق بالتفصيل في القسم 6/7 هنا زي المعتاد —
الـ CHANGELOG نفسه سطر واحد لكل جولة بس، مش تكرار للتفاصيل.

---

## 9. Known Gaps from Test Environment

نتائج فعلية من أول تشغيل حقيقي لـ `generic_spider.py` ضد
`test-environment/`'s الكامل (mock-target خلف Anubis) —
`src/spiders/configs/mock_target.yaml`، `tests/integration/test_mock_target_live.py`.
مطابق لتوقّع القسم 5 الصريح في تعليمات بناء بيئة الاختبار: "مش
المفروض ينجح من أول مرة بالكامل" — التفاصيل الكاملة (مش ملخّص) موجودة
في docstring الاختبار نفسه؛ هنا الفهرس/التصنيف الرسمي.

### 1. Byparr's CI `services:` container can't reach the compose stack at all — ✅ اتحل (round 2, 2026-08-21)

**الفجوة (دي اللي فعليًا بتحصل في CI الحقيقي، run
[32479883962](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32479883962)
— مش افتراض محلي):** `ByparrMiddleware` بيبعت لـ Byparr
`url=http://localhost:8080/` (بورت Anubis المنشور). بس Byparr شغّال في
CI كـ `services:` container منفصل تمامًا — على شبكة Docker خاصة بيه،
غير شبكتي `test-environment`/`edge` بتوع `docker-compose.test.yml`
خالص. جوه container بتاع Byparr نفسه، `localhost` معناها container
Byparr نفسه، مش الـ runner، فمتصفح Byparr بيرجّع
`NS_ERROR_CONNECTION_REFUSED` — **متأكّد منه مباشرة من لوج Byparr's
service container نفسه في الـ run الحقيقي ده**، مرتين بالظبط (مرة لكل
اختبار في `test_mock_target_live.py`):
```
ERROR: Could not reach the target: Page.goto: NS_ERROR_CONNECTION_REFUSED
navigating to "http://localhost:8080/", waiting until "load"
```
`ByparrProvider.solve()` بيرمي `AntibotError`، `ByparrMiddleware`
بيسجّل `byparr_middleware.solve_failed_fallback` ويرجع لـ plain Scrapy
download — واللي **فعلاً بيوصل لـ Anubis** (لأن Scrapy نفسه شغّال
مباشرة على الـ runner، مش جوه container، فـ `localhost:8080` بتاعه هو
فعلاً المنفذ المنشور من `docker compose`).

**وهنا فجوة تانية مستقلة بتظهر (اتأكّد منها بـ `curl` عادي بردو، مفيهوش
Scrapy خالص):** Anubis's الـ policy الافتراضية الحقيقية
(`anubis/botPolicy.yaml`، نسخة حرفية من الأصل، مش معدّلة) بترفض
User-Agent الافتراضي بتاع Scrapy (`Scrapy/2.18.0 (+https://scrapy.org)`)
صراحة عبر قاعدة `bot/ai-catchall`
(`"msg":"explicit deny", "check_result":{"name":"bot/ai-catchall","rule":"DENY"}`)
— أي bot بيعرّف نفسه بصراحة في الـ User-Agent (زي Scrapy المهذّب) هو
بالظبط اللي القايمة دي مصمّمة تلاحقه. بالمقابل، `curl` بـ User-Agent
مجهول (`curl/8.5.0`) بيعدي من غير أي تحدي خالص (`weight <= 0` →
"minimal-suspicion" → ALLOW، لأن مفيش قاعدة "generic-bot-catchall"
مفعّلة افتراضيًا في نسخة Anubis الأصلية).

**النتيجة العملية في CI:** الكراول بيخلّص بنجاح (مفيش crash)، لكن
بصفر items — طلب Scrapy المباشر (fallback) بيترفض صراحة من Anubis، مش
حتى بيوصل لصفحة تحدي. `test_mock_target_yields_zero_items_stuck_behind_anubis_challenge`
بيوثّق ده كنتيجة حقيقية متوقعة ومؤكدة في CI، مش aspiration.

**✅ تحديث (round 2, 2026-08-21) — اتحل فعليًا:** بدل ما نديله عنوان
الـ runner، `docker-compose.test.yml` دلوقتي بيشغّل `byparr` instance
خاص بيه (منفصل تمامًا عن `services: byparr` الأصلي بتاع
`docker-compose.yml`، اللي فاضل زي ما هو لكل الاختبارات التانية) بـ
`network_mode: "service:anubis"` — يشارك نفس network namespace بتاع
Anubis container حرفيًا، فـ `localhost:8080` جوه container Byparr
نفسه بقى فعلاً هو Anubis. اتأكّد منه يدويًا: نفس الـ URL بالظبط
(`http://localhost:8080/`) اللي كان بيرجّع `NS_ERROR_CONNECTION_REFUSED`
بقى بيوصل فعليًا لـ Anubis ويستلم تحدي proof-of-work حقيقي (لوج
Anubis: `"msg":"new challenge issued"`, weight=10). الفجوة دي اتقفلت
رسميًا — التفاصيل والنتيجة الكاملة (وصل التحدي، بس لسه مقدرش يعديه لسبب
تالت جديد) في بند 4 تحت.

### 2. Anubis's challenge cookie required Secure/Partitioned attributes not usable over plain HTTP — ✅ اتحل (round 2, 2026-08-21)

**فجوة تانية مستقلة، اتأكّد منها محليًا (Byparr وAnubis على نفس شبكة
Docker، بعنونة container name — مش السيناريو الحقيقي في CI أعلاه، بس
سيناريو محتمل لو بند 1 اتصلح):** لما Byparr فعلاً قدر يوصل لـ Anubis
(3 محاولات متطابقة محليًا، مش flaky)، ده حصل عليه تحدي proof-of-work
حقيقي (`weight=10` بسبب الـ User-Agent البراوزري بتاع Byparr،
threshold "moderate-suspicion"، لوج Anubis: `"msg":"new challenge issued"`)
— لكن **مقدرش يعدّي التحدي خالص**. السبب الجذري اتأكّد منه مباشرة
بقراءة `Set-Cookie` headers بتاع Anubis نفسه: الكوكيز اللي بتثبت
اجتياز التحدي (`techaro.lol-anubis-cookie-verification-*`) معمولة
`Secure; SameSite=None` — يعني أي براوزر حقيقي (بما فيه Chromium اللي
Byparr بيشغّله) هيرفض يحتفظ بيها فوق `http://` عادي. Anubis's لوج
بتاعه بيأكد نفس الحاجة حرفيًا: `"msg":"user has cookies disabled, this
is not an anubis bug"`.

**يعني عمليًا:** حتى لو بند 1 (فجوة الشبكة في CI) اتصلح، التحدي لسه
مش هيكتمل من غير TLS — الفجوتين لازم يتصلحوا مع بعض عشان Byparr
يعدّي Anubis فعليًا.

**ده مش باج في GenericSpider ولا ByparrMiddleware ولا Byparr نفسه** —
كان فجوة نشر حقيقية: `test-environment/`'s stack شغّال HTTP بس.

**✅ تحديث (round 2, 2026-08-21) — اتحل فعليًا، بدون TLS:** بدل ما
نضيف TLS (اللي كان هيحتاج container/شهادة إضافية، وبعدين المتصفح اللي
Byparr بيشغّله لازم يثق فيها كمان — سطح فشل جديد غير متحقق منه لفايدة
مش واضحة)، اتفحص الكود المصدري الحقيقي بتاع Anubis
(`cmd/anubis/main.go`) لقاء دليل حقيقي: `cookie-secure` (env
`COOKIE_SECURE`, default `true`) و`cookie-partitioned` (env
`COOKIE_PARTITIONED`, default `true`) flags رسميين وموثّقين — مش
workaround. ده الطريقة الرسمية المدعومة لتشغيل Anubis من غير TLS
أصلاً (Anubis نفسه مفهوش HTTPS server مدمج خالص، اتأكّد منه من نفس
الكود المصدري). ضبط الاتنين `false` في `docker-compose.test.yml`،
واتّحقّق منه يدويًا فعليًا عبر `curl`'s `Set-Cookie` headers: `Secure`
اختفى، و`SameSite` رجع أوتوماتيك من `None` لـ `Lax` بالظبط زي ما
الـ flag help text بتاع Anubis بيقول هيحصل.

**بس ده وحده مكانش كافي** — `COOKIE_SECURE=false` لوحده فضل النتيجة
زي ما هي ("cookies disabled")؛ لازم `COOKIE_PARTITIONED=false` كمان
— على الأرجح لأن CHIPS (Partitioned cookies) مش كل سياق automation
بيتعامل معاه زي browser profile عادي. الاتنين مع بعض هما اللي
اتّحقّق منهم فعليًا.

**النتيجة:** الفجوة دي (Secure cookie فوق HTTP) اتقفلت رسميًا. بس
اكتشفنا فجوة تالتة جديدة بعد ما دي اتحلت — بند 4 تحت.

### 3. Real code fix that came out of round 1: `TITAN_BYPARR_URL` env-var fallback

**اكتشاف حقيقي أثناء التكامل:** ولا config قبل كده كان مستخدم
`antibot_needed: true` — أول مرة اتحقق فعليًا إن
`ByparrMiddleware.from_crawler()` بيقرا `TITAN_BYPARR_URL` بس من
`crawler.settings` (اللي بيتملى تلقائيًا من متغيرات بيئة بادئتها
`SCRAPY_` بس، مش `TITAN_*`) — يعني تشغيل `scrapy runspider` حقيقي
ومعاه `TITAN_BYPARR_URL` كـ environment variable عادي (بالظبط زي
`.github/workflows/ci.yml`'s الـ job-level env) **مكانش هيوصّل الـ
URL لـ ByparrMiddleware خالص**، وكان هيرجع لـ fallback صامت
(`byparr_middleware.not_configured_fallback`) من غير أي خطأ ظاهر.

**اتصلح فورًا (نصلح دلوقتي، مش نأجل)** — `src/middlewares/byparr_middleware.py`
دلوقتي بيرجع لـ `os.environ.get("TITAN_BYPARR_URL")` لو
`crawler.settings` مرجّعش حاجة، بنفس نمط `src/settings.py`'s
env-driven pattern. unit tests جديدة (`test_from_crawler_falls_back_to_the_os_environment_variable`)
وتعديل الاختبارين القديمين عشان يبقوا deterministic (`monkeypatch.delenv`)
بغض النظر عن الـ environment الفعلي اللي الاختبارات شغالة فيه — مُتحقّق
منه فعليًا محليًا (7/7 tests PASSED) قبل الدفع.

### 4. Byparr tears its browser down before Anubis's async challenge JS runs — فجوة جديدة حقيقية، لسه مفتوحة (round 2, 2026-08-21)

**اكتشفت بس بعد ما بند 1 و2 اتحلوا فعليًا** — مش قبل كده، لأن قبل
كده Byparr مكانش بيوصل لـ Anubis أصلاً. دلوقتي بيوصل فعليًا ويستلم
تحدي proof-of-work حقيقي في كل محاولة (لوج Anubis:
`"msg":"new challenge issued"`، weight=10)، بس **لسه مقدرش يكمّله**
— لسبب مختلف تمامًا عن الكوكيز.

**السبب الجذري (اتأكّد منه يدويًا، مش افتراض):** راقبت لوج Anubis
لأكتر من 20 ثانية بعد ما `ByparrProvider.solve()` خلص ورجع نتيجة —
**مفيش أي حدث تاني بيحصل خالص**: مفيش `pass-challenge` call، مفيش
تحذير "cookies disabled"، مفيش proxy request — سكوت تام. تحدي Anubis
الحقيقي بيتحسب **async في المتصفح بعد ما حدث `load` يحصل** (JS بيبعت
النتيجة لـ endpoint تاني، وده بياخد وقت حقيقي حتى لو صغير). Byparr's
`/v1` API بيرجع النتيجة فور ما `load` يحصل (لوجه نفسه بيقول
`waiting until "load"`) — وبيقفل الـ browser context فورًا، قبل ما
الروتين الـ async ده ياخد فرصة يشتغل خالص.

**ده نفس شكل فجوة `render_wait_ms` اللي اتحلت قبل كده لـ
`PlaywrightMiddleware`** (موقع بيعمل شغل حقيقي بعد `load` event،
محتاج انتظار إضافي صريح) — بس هنا لازم تتحل في `ByparrProvider`/Byparr
نفسه مش `PlaywrightMiddleware` (لأن `antibot_needed: true` بيوجّه
الطلب لـ Byparr مش Playwright). Byparr's بروتوكول (README بتاعه،
اتفحص فعليًا) **مفيهوش** أي parameter زي "استنى X ملي ثانية كمان بعد
الـ load" أضيفه.

**لو قررنا نصلحها بعدين:** كود جديد حقيقي، احتمالين:
- إضافة polling/retry منطق في `ByparrProvider` (لو Byparr بيدعم
  session-based requests بدل stateless — محتاج فحص إضافي لـ Byparr's
  API الفعلي، `/docs` بتاعه، مش موثّق في الـ README)
- أو التواصل مع مشروع Byparr نفسه (upstream) لإضافة parameter زي
  `postLoadWaitMs` لبروتوكول `/v1`

**القرار (نفس تصنيف دورة التصعيد):** نأجل ونسجل — الفجوة دي محتاجة
بحث/كود جديد حقيقي في provider مش تحت سيطرتنا بالكامل (Byparr نفسه
third-party)، مش تعديل سريع.

**النتيجة العملية:** الكراول لسه بيخلّص بصفر items (مفيش crash)، بس
دلوقتي لسبب مختلف تمامًا وأعمق من الجولة الأولى — بيوصل فعليًا لتحدي
Anubis الحقيقي كل مرة، بس مقدرش يكمّله. تقدّم حقيقي بمقياس دورة
التصعيد (قسم 8)، مش دليل إن الفجوة اتقفلت.

**تحديث (round 3، 2026-08-21):** الفجوة دي **لسه مفتوحة بالنسبة لـ
Byparr نفسه** (upstream، مش تحت سيطرتنا — issue مقترح مسجّل، راجع
"Antibot Provider Comparison" تحت لتفاصيل الطلب المقترح لمشروع
Byparr). بس دلوقتي عندنا **بديل حقيقي شغّال**: `CamoufoxProvider`
(بند 5 تحت) — بيحل بالظبط نفس نوع التحدي ده لأنه بيتحكم في توقيت
إغلاق المتصفح بنفسه. الفرق الكامل بين الاتنين موثّق في "Antibot
Provider Comparison" تحت، بناءً على نتايج حقيقية مش افتراض.

### 5. CamoufoxProvider crashed outright: Playwright's sync API can't share a thread with Scrapy's asyncio reactor — ✅ اتحل (round 3, 2026-08-21)

**الفجوة (اكتشفت فعليًا في CI run
[32503723559](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32503723559)،
مش افتراض — الدليل جه من diagnostic logging اتضاف فورًا لما النتيجة
الأولى كانت 0 items من غير أي سبب واضح):** `CamoufoxProvider.solve()`
كان بيتنادى مباشرة من جوه `ByparrMiddleware.process_request()` —
synchronous، على نفس الـ thread بتاع Scrapy's asyncio reactor. Camoufox
(زي Playwright نفسه) بيرفض يشتغل كده، ورمى استثناء واضح جدًا فوري:
```
camoufox failed to solve http://localhost:8080/: It looks like you are
using Playwright Sync API inside the asyncio loop.
Please use the Async API instead.
```
يعني الكراول كان بيفشل فورًا (مش حتى بيوصل لـ Anubis بشكل حقيقي)،
والنتيجة (0 items) كانت بتوهم إن المشكلة في التحدي نفسه — لغاية ما
diagnostic logging (`camoufox_provider.solved`/`solve_failed` +
طباعة stderr الحقيقي في `run_spider_live`) كشفت السبب الحقيقي.

**ليه المعمارية الحالية مش كانت قادرة:** `PlaywrightMiddleware` عنده
بالظبط نفس القيد ده من زمان، وحلّه بالفعل عبر `deferToThread` (تشغيل
الـ renderer على thread منفصل، مش على reactor thread) —
`ByparrMiddleware` مكانش بيعمل نفس الحاجة لأن `ByparrProvider`'s HTTP
call العادي (`urllib`) مالوش نفس القيد، فمكانش محتاج thread hop. لما
`CamoufoxProvider` (أول provider بيشغّل Playwright حقيقي) اتضاف، نفس
القيد ظهر تاني.

**✅ الحل:** `ByparrMiddleware.process_request()` دلوقتي بيرجّع نتيجة
`self._thread_runner(self._solve, request)` — نفس الشكل بالظبط بتاع
`PlaywrightMiddleware` (`deferToThread` افتراضيًا، قابل للحقن
لل unit tests زي `_sync_thread_runner`). ده كمان بيحسّن حاجة تانية
جانبية: طلب Byparr HTTP البطيء نفسه مش هيوقف الـ reactor thread تاني.
unit test جديد (`test_process_request_defaults_to_a_real_thread_runner`)
بيتأكد إن النتيجة الافتراضية فعلاً `Deferred` حقيقي، مش استدعاء مباشر.

**النتيجة العملية:** الفجوة دي (crash فوري) اتقفلت رسميًا — الاختبار
اتّحقّق منه محليًا (145 unit tests + 14 contract tests PASSED) قبل
الدفع.

**بعد ما الـ crash اتحل، النتيجة الحقيقية في CI run
[32505555570](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32505555570)
كانت تقدّم حقيقي وواضح:** `camoufox_provider.solved` (diagnostic
logging اتضاف مخصوص للجولة دي) أكد إن Camoufox فعلاً حسب واستلم حل
proof-of-work حقيقي (nonce/response hash/elapsedTime=171ms ظاهرين في
الـ URL النهائي، `.../pass-challenge?id=...&response=...&nonce=431&elapsedTime=171`)
— يعني التحدي بقى بيتحل فعليًا، مش مجرد وصول. بس الصفحة النهائية
كانت لسه "Oh noes!" (صفحة خطأ Anubis)، بـ`cookie_names: []`.

**السبب الجذري النهائي (اتأكّد منه من لوج Anubis نفسه بعد ما
`docker compose logs anubis` اتضاف لخطوة الـ CI، run
[32506681634](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32506681634)):**
```
"msg":"user has cookies disabled, this is not an anubis bug"
```
**نفس رسالة بند 2 بالظبط.** السبب: `COOKIE_PARTITIONED=false` كان
**اتّحقّق منه يدويًا في الجولة التانية (round 2) بس ما اتضافش فعليًا
لـ `docker-compose.test.yml`** — غلطة حقيقية (oversight)، مش قرار
متعمد: بند 2 وثّق إن الفلاجين الاتنين (`COOKIE_SECURE` و
`COOKIE_PARTITIONED`) لازم يتظبطوا مع بعض، بس الـ commit وقتها ضاف
`COOKIE_SECURE` بس. **✅ اتصلح فعليًا دلوقتي** — `COOKIE_PARTITIONED: "false"`
اتضاف لـ `docker-compose.test.yml`.

**✅✅ تأكيد نهائي (CI run [32507637737](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32507637737)):**
بعد الفيكسين الاتنين مع بعض (deferToThread + `COOKIE_PARTITIONED=false`)،
`test_mock_target_camoufox_gets_past_anubis_and_yields_real_posts`
نجح فعليًا — **21/21 اختبار PASSED**، Camoufox عدّى تحدي Anubis
الحقيقي بالكامل ورجّع posts حقيقية (مش صفر items) من `mock_target_camoufox.yaml`.
الفجوة دي (بند 4 فوق، مأخوذة بمنظور Camoufox) اتقفلت رسميًا — أول
اختبار حقيقي لمرونة `antibot_provider` نجح، وموثّق كـ round 3 في
`test-environment/CHANGELOG.md`.

### 6. تصحيح فرضية: Camoufox مش "نفس محرك Byparr من غير القيد" — اتفحصت فعليًا ورجعت غلط

**الفرضية اللي جت في طلب تعديل المهمة:** إن `CamoufoxProvider` بيستخدم
"نفس المحرك اللي Byparr بيستخدمه، من غير القيد اللي في طبقة API بتاعته
تحديدًا" — يعني الفرق مجرد طبقة API، مش محرك مختلف.

**اتفحصت فعليًا قبل ما تتوثّق أو يتصدّقها حد** (مبدأ "verify, don't
assume" — مش بس على نتايج الكود، على أي فرضية بتتكتب في التوثيق كمان):
- `pyproject.toml` الحقيقي بتاع Byparr نفسه
  (`raw.githubusercontent.com/thephaseless/Byparr/main/pyproject.toml`)
  بيعتمد على `playwright==1.60.*` + `invisible-playwright>=0.6.1` +
  `playwright-captcha==0.1.*` — **مفيش `camoufox` package خالص جواه**.
- `invisible-playwright` نفسه (patched-Firefox project منفصل تمامًا،
  اتفحص عبر PyPI metadata بتاعه) بيذكر Camoufox صراحة كـ *مشروع
  للمقارنة* في التوثيق بتاعه، مش كـ dependency مشترك.

**يعني الاتنين (Camoufox و Byparr) بيستخدموا محركين Firefox-stealth
مختلفين وغير مرتبطين ببعض خالص** — مش "نفس المحرك من غير قيد API"،
ده تغيير محرك حقيقي، مش مجرد تخطي طبقة Byparr بيلفها حوالين نفس
المتصفح.

**اللي هو صح فعلاً، وده السبب الحقيقي اللي `CamoufoxProvider` موجود
عشانه:** `ByparrProvider` بيتفوّض بالكامل لخدمة Byparr's HTTP خارجية
ومالوش أي سيطرة على توقيت إغلاق متصفحها — ده قيد في **عقد API بتاع
Byparr نفسه** (بيقفل فور `load` event، بند 4 فوق)، مش خاصية في محرك
معيّن. `CamoufoxProvider` بيحل المشكلة دي عن طريق إنه يشغّل متصفحه
بنفسه in-process، مش لأنه "نفس محرك Byparr".

**✅ اتصلح في التوثيق:** module docstring بتاع
`src/providers/antibot/camoufox_provider.py` اتعدّل عشان يوضّح ده
صراحة (تصحيح مكتوب بالظبط، مش حذف الفرضية بصمت)، بدل ما يتكتب كود جديد
بناءً على فرضية غلط. راجعت باقي التوثيق (القسم ده، "Antibot Provider
Comparison" تحت) للتأكد إنه معندوش نفس الصياغة الغلط — مفيش.

### 7. PatchrightProvider — خيار تالت أخف (Chromium + stealth layer فوق Playwright الموجود)

**إضافة جديدة (تعديل المهمة، هذه الجولة):** `PatchrightProvider`
(`src/providers/antibot/patchright_provider.py`) — implementation تالت
لـ `AntibotProvider`، بنفس معمارية `CamoufoxProvider` بالظبط (نفس
العقد، `solve_fn` قابل للحقن، `post_load_wait_ms` قابل للتهيئة، نفس
diagnostic logging)، بس بيستخدم `patchright.sync_api.sync_playwright()`
+ `p.chromium.launch()` — Patchright نفسه (`patchright` PyPI package،
v1.62.1 وقت الكتابة، Apache-2.0) هو "drop-in replacement" حقيقي
لـ Playwright (اتأكّد منه فعليًا عبر PyPI metadata + README بتاعه قبل
الكتابة)، فهو فعليًا **إعادة استخدام لـ Playwright الموجود بالفعل في
المشروع (`playwright_middleware.py`) + طبقة stealth فوقه** — مش محرك
جديد تمامًا زي Camoufox (Chromium بدل Firefox)، وده أساس كونه "أخف".

Chromium-only، ومحتاج binary منفصل بتاعه (`patchright install
chromium` — مش نفس binary بتاع `playwright install`، حتى لو نفس
المتصفح الأساسي). contract tests + unit tests (6 اختبارات، نفس نمط
Camoufox: happy path + 5 حالات فشل/حواف) + دمجه في `SpiderConfig`'s
`antibot_provider` Literal + `ByparrMiddleware`'s dispatch dict +
`from_crawler()` + خطوة CI جديدة (`patchright install chromium`) —
كله اتّحقّق منه محليًا (ruff/mypy --strict/pytest unit+contract) قبل
الدفع.

**`mock_target_patchright.yaml` + `test_mock_target_patchright_live.py`**
اتضافوا لتشغيله فعليًا ضد نفس تحدي Anubis اللي Byparr فشل فيه
وCamoufox عدّاه.

**❌ النتيجة الحقيقية (CI run [32524934383](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32524934383)):
Patchright فشل — بس بسبب مختلف تمامًا وأعمق من فجوة Byparr.**
`patchright_provider.solved`'s diagnostic log نفسه أظهر
`"title": "Oh noes!"` (صفحة رفض Anubis) و`"cookie_names": []`. لوج
Anubis نفسه (اللي اتضاف للـ CI مخصوص لهذا النوع من التشخيص) أوضّح
السبب الجذري بالظبط:
```
"msg":"explicit deny", ... "check_result":{"name":"bot/headless-chrome","rule":"DENY","weight":0}
```
Anubis عنده rule مدمج بيتعرّف على fingerprint بتاع headless Chromium
وبيرفضه صراحة — **قبل ما يوصل حتى لمرحلة تحدي الـ proof-of-work
خالص**. طبقة stealth بتاعة Patchright بتعدّل fingerprints Chromium's
automation، بس مش كفاية عشان تتخطى الـ rule ده تحديدًا في الـ
deployment الحقيقي ده. الفرق الجوهري عن فجوة Byparr (بند 4): Byparr
بيوصل فعليًا للتحدي ويستلمه بس بيقفل المتصفح قبل ما يكمّله (فجوة
توقيت)، أما Patchright فمبيوصلش للتحدي أصلاً (fingerprint match) —
يعني `post_load_wait_ms` (السبب الأساسي اللي Patchright موجود
عشانه، زي Camoufox بالظبط) **معندوش فرصة يأثّر خالص هنا**، لأن
الطلب اتّرفض قبل ما `load` حتى يحصل.

**القرار:** بدل ما يفضل الاختبار فاشل (أحمر) في CI، اتحوّل لنفس
النمط اللي `test_mock_target_live.py` بالفعل بيستخدمه لفجوة Byparr
المؤكدة — assertion بيوثّق النتيجة الحقيقية المعروفة (صفر items،
بسبب rejection صريح من Anubis) كـ regression sentinel، مش أمل. لو
النتيجة اتغيّرت مستقبلًا (rule بتاع Anubis اتغيّر، أو طبقة stealth
بتاعة Patchright اتحسّنت)، الاختبار يتحدّث وقتها.

**قرار nodriver (شرط المستخدم الصريح: "لو الاتنين فشلوا مع
Anubis"):** الشرط ده **مش متحقق** — Camoufox نجح فعليًا (round 3،
run 32507637737)، فمش الاتنين فشلوا، Patchright بس. عندنا بالفعل
بديل حقيقي شغّال لنفس نوع التحدي ده (Camoufox). **مفيش داعي لـ
nodriver دلوقتي** بناءً على الشرط اللي اتحدد، مش قرار تقني إن
nodriver مش هيفرق — لو المستخدم عايز redundancy إضافي أو تحدي مستقبلي
تاني يحتاجه، ده قرار منفصل.

### 8. Cookie-consent wall + click_selector في AntibotProvider — ✅ اتأكّد فعليًا (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md، بند 1)

**الإضافة:** `AntibotProvider.solve()` اتوسّع بـ `click_selector`
اختياري (best-effort، مش جزء من العقد الإلزامي) — `CamoufoxProvider`
و`PatchrightProvider` بيعرفوا يعملوا click حقيقي (بيشغّلوا متصفح
حقيقي)، `ByparrProvider` بيسجّل تحذير واضح ومكمّل من غير click (الـ
`/v1` API بتاعه مفيهوش أي قدرة تفاعل/click خالص — فجوة حقيقية، مش
تجاهل صامت). `ByparrMiddleware` بيمرّر `request.meta["click_selector"]`
(اللي `GenericSpider` بيحطه في كل طلب أصلاً) على طول.

على مستوى البيئة: `test-environment/mock-target` عنده دلوقتي cookie
consent wall حقيقي (`structural/cookie_wall.py`) — المحتوى الحقيقي
غايب تمامًا من الاستجابة لحد ما consent cookie تتحط عبر رابط "Accept"
حقيقي، مش overlay بـ CSS (اللي كان هيتهزم بسهولة بأي scraper
selector-based مش بيتحقق من الـ visibility). الـ 3 configs
(`mock_target*.yaml`) اتحدّثوا بـ `click_selector: "#accept-cookies"`.

**✅✅ النتيجة الحقيقية (CI run [32528886186](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32528886186)، 23/23 اختبار PASSED):**
- **Camoufox**: `test_mock_target_camoufox_gets_past_anubis_and_yields_real_posts`
  نجح فعليًا — يعني Camoufox عدّى الاتنين مع بعض: تحدي Anubis
  الحقيقي (زي round 3) **وبعده** cookie wall الجديد، عن طريق click
  حقيقي على `#accept-cookies` وانتظار `post_load_wait_ms` بعد كده —
  أول تأكيد حقيقي إن `click_selector` بتاع `CamoufoxProvider` شغّال
  فعليًا ضد gate حقيقي متراكب (stacked)، مش بس نظريًا.
- **Byparr**: `test_mock_target_yields_zero_items_stuck_behind_anubis_challenge`
  نجح (لسه بيرجع صفر items) — **زي ما كان متوقّع**: Byparr بيفشل
  أصلاً في مرحلة Anubis (بند 4) قبل ما يوصل لـ cookie wall خالص،
  فمفيش معلومة جديدة تتقاس هنا عن click_selector تحديدًا؛ ده مسجّل
  صراحة، مش مفترض إنه "نجح" أو "فشل" بخصوص الفجوة الجديدة.
- **Patchright**: `test_mock_target_patchright_yields_zero_items_denied_by_anubis`
  نجح (لسه بيرجع صفر items) — **زي ما كان متوقّع** لنفس السبب: مرفوض
  صراحة من Anubis's `bot/headless-chrome` rule (بند 7) قبل حتى
  `load` يحصل، فـ`click_selector` بتاعه (رغم إنه مبني فعليًا)
  معندوش فرصة يتنفّذ في البيئة دي تحديدًا.

**الخلاصة:** الطبقة الأساسية (`click_selector` في العقد + التنفيذ
الحقيقي في Camoufox/Patchright) اتبنت وموثّقة **وشغّالة فعليًا** —
اتأكّد منها بدليل حقيقي (Camoufox)، مش بس نظريًا. Byparr/Patchright's
نتيجتهم مع cookie wall فضلت **غير قابلة للتمييز** عن فجوة Anubis
الأسبق بتاعتهم — موثّق صراحة، مش مخفي.

### 9. JSON/API parsing support في GenericSpider — ✅✅✅ اتحل فعليًا (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md، بند 4)

**الإضافة:** `SpiderConfig` كسب `response_format: "html" | "json"` +
`json_selectors` (dotted-key paths زي `"post.author"` بدل CSS selectors،
+ `next_cursor_path`/`has_next_page_path` للـ pagination) — validated
بحيث بالظبط واحد من `selectors`/`json_selectors` يكون موجود حسب
`response_format` (`model_validator`). `GenericSpider.parse()` بيتفرّع
لـ `_parse_html` (الأصلي) أو `_parse_json` الجديد — بيستخدم
`response.json()` (Scrapy's `TextResponse`)، بيمشي في dotted paths عبر
`_resolve_json_path`، وبيبني رابط الصفحة الجاية عبر `_build_next_json_url`
(`?after=<cursor>`، مطابق تمامًا لبروتوكول `/api/feed`'s الحقيقي).
9 unit tests جديدة (happy path + pagination + JSON مش صحيح + items_path
مش list + field غايب بيرجع None) + 5 unit tests لـ `SpiderConfig`'s
validator الجديد.

`src/spiders/configs/mock_target_feed.yaml` — أول target حقيقي، بيشاور
على `/api/feed` (اللي اتبنى من الجولة الأولى بس ما كانش متوصّل لـ
`GenericSpider`). زي `/`، `/api/feed` وراء Anubis برضه، فمحتاج
`antibot_provider: camoufox` (الوحيد المؤكد إنه بيعدّي Anubis فعليًا).

**سؤال حقيقي كان مفتوح:** Camoufox بيشغّل Firefox حقيقي، وFirefox عنده
viewer مدمج ممكن يحوّل الـ DOM المعروض لصفحة JSON response خام — هل
`response.json()` هيفضل يشتغل صح ضد الـ DOM المتحوّل ده؟

**❌ النتيجة الحقيقية (CI run [32656904590](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32656904590)):
فشل — والسبب الجذري اتأكّد بالدليل، مش افتراض.** لوج
`camoufox_provider.solved` أظهر إن Camoufox عدّى Anubis فعليًا بنجاح
(status 200، html_length 7433، كوكيز Anubis الحقيقية موجودة) — يعني
مفيش مشكلة في الوصول خالص. بس `generic_spider.invalid_json` طلع بعده
على طول، وبعد ما أضفنا diagnostic logging لأول 300 حرف من الـ body
الفعلي (commit `b2ef256`)، ده اللي رجع فعليًا:
```
<html><head><link rel="stylesheet" href="resource://content-accessible/plaintext.css"></head><body><pre>{"edges":[{"comments":[{"author":"delgadotiffany",...
```
**تأكيد كامل للفرضية:** Firefox (اللي Camoufox بيشغّله) بيلف أي
استجابة `application/json` بغلاف HTML خاص بيه (`<html><body><pre>` +
`plaintext.css`) قبل ما `page.content()` (اللي `CamoufoxProvider`
بيرجّعه) يتقرا خالص. `response.json()` بيشوف الغلاف ده، مش الـ JSON
الخام، فبيرفضه بحق — كود الـ parsing نفسه اشتغل صح 100% (معالج الفشل
اشتغل زي ما اتصمم، مفيش crash)، الفجوة في مصدر البيانات (الـ provider)
مش في منطق الـ parsing.

**القرار وقتها: نأجل ونسجل** — سجّلنا الفجوة بالدليل بدل ما نحاول
نصلحها بطريقة مصطنعة في نفس جولة الاكتشاف. الاختبار الحي اتحوّل لنمط
regression sentinel (زي Byparr/Patchright) لحد ما الحل يتنفّذ.

**✅ الحل (جولة تالية مباشرة، بمجرد ما اتطلب صراحة):** الحل الحقيقي
المتوقع من التشخيص فوق نُفّذ بالظبط زي ما اتوصّف — `_default_camoufox_solve`
(و`_default_patchright_solve` بنفس المبدأ، رغم إن Patchright مايوصلش
لـ endpoint من نوع JSON في البيئة دي أصلاً، بند 7) دلوقتي بيتحقق من
`content-type` header بتاع الاستجابة الحقيقية؛ لو `application/json`،
بيستخدم `response.text()` بتاع Playwright (الـ raw network body، اللي
ماتلمسوش أي DOM rendering خالص) بدل `page.content()` — أي content-type
تاني فاضل يستخدم `page.content()` زي ما كان بالظبط، مفيش تغيير في
سلوك الصفحات العادية. اللوج (`camoufox_provider.solved`/`patchright_provider.solved`)
دلوقتي بيسجّل `content_type` و`used_raw_network_body` كدليل مباشر.

الاختبار الحي رجع لصيغته الأصلية (يتوقع نتايج حقيقية من `/api/feed`،
مش صفر items) — `test_mock_target_feed_yields_real_posts_from_the_json_api`.
اتّحقّق محليًا (ruff/mypy --strict/189 unit+contract test PASSED، 89.24%
coverage) قبل الدفع.

**❌ محاولة الحل الأولى فشلت فعليًا (CI run [32660273266](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32660273266)) — بسبب جذري تاني، اتأكّد بالدليل مش افتراض:**
اللوج أظهر `"content_type": "text/html; charset=utf-8", "used_raw_network_body": false`
— يعني الشرط `application/json` ماتحققش خالص، فالكود رجع يستخدم
`page.content()` القديم. **السبب:** `response = page.goto(url, ...)`
بيرجّع استجابة صفحة **تحدي Anubis المؤقتة** (`content-type: text/html`)،
مش الهدف الحقيقي — Anubis's الحقيقي بيوجّه للمحتوى الفعلي (JSON) بشكل
**async من جوه الـ JS بعد ما الـ challenge يتحل**، بعد ما `page.goto()`
يكون رجع خلاص. نفس شكل فجوة "async بعد load" بالظبط اللي `post_load_wait_ms`
موجود أصلاً عشانها (بند 4) — بس هنا أثّرت على *الـ response object*
نفسه مش بس على المحتوى المرئي.

**✅✅ الحل الصحيح (تصحيح مباشر، بدليل السبب الحقيقي):** `_default_camoufox_solve`
و`_default_patchright_solve` دلوقتي بيسجّلوا **كل** استجابات الـ
main-frame عبر `page.on("response", ...)`، ومش بيعتمدوا على أول
استجابة من `goto()` بس — بياخدوا **آخر** استجابة main-frame (اللي
بتعكس أي redirect حصل بعد الـ challenge)، وبيتحققوا من الـ content-type
بتاعها هي. دلوقتي `content_type`/`used_raw_network_body` في اللوج
بيعكسوا الاستجابة الحقيقية النهائية، مش الأولى. اتّحقّق محليًا (ruff/mypy
--strict/189 unit+contract test PASSED، 87.82% coverage) قبل الدفع.

**❌ محاولة الحل التانية فشلت برضه — بس المهم: فشلت بطريقة مختلفة
تمامًا، كشفت regression حقيقي في الكود نفسه (CI run [32662253990](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32662253990)):**
مش اختبار JSON هو اللي فشل هذه المرة — `test_mock_target_camoufox_gets_past_anubis_and_yields_real_posts`
(اختبار الـ cookie wall المؤكد ناجح من قبل) فشل بـ:
```
ValueError: Cannot use css on a Selector of type 'json'
```
**السبب الجذري (اتأكّد بقراءة الـ traceback مباشرة، مش تخمين):**
الفلتر `resp.frame is page.main_frame` مش كافي لتحديد "هل ده تحميل
صفحة حقيقي" — أي طلب `fetch`/XHR بتعمله JS الصفحة نفسها (زي طلب
Anubis's الحقيقي لـ `pass-challenge` API، اللي بيرجّع JSON) بيشارك
نفس الـ `.frame` بتاع الصفحة الرئيسية، فكان بيتسجّل كـ "آخر استجابة"
ويطغى على استجابة الصفحة الحقيقية بالكامل — حتى لما الهدف كان HTML
عادي مش JSON خالص.

**✅✅✅ الحل الصحيح النهائي:** أضفنا شرط `resp.request.is_navigation_request()`
جنب فحص الـ frame — كده بس تحميلات الصفحة الحقيقية (navigation) بتتسجّل،
مش أي طلب فرعي بتعمله JS الصفحة. اتّحقّق محليًا (ruff/mypy --strict/189
unit+contract test PASSED، 87.82% coverage) قبل الدفع.

**✅✅✅ اتأكّد فعليًا (CI run [32664864782](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32664864782)) — مش افتراض، دليل حقيقي من الـ job logs:**
`24 passed, 2 warnings in 276.88s (0:04:36)` — صفر FAILED في اللوج
كله. الاختبارين اللي كانوا في قلب الموضوع اتأكّدوا صراحة PASSED في
نفس الـ run:
```
tests/integration/test_mock_target_camoufox_live.py::test_mock_target_camoufox_gets_past_anubis_and_yields_real_posts PASSED
tests/integration/test_mock_target_feed_live.py::test_mock_target_feed_yields_real_posts_from_the_json_api PASSED
```
يعني: (1) اختبار الـ JSON API نفسه بيرجع بيانات حقيقية من `/api/feed`
دلوقتي (الفجوة الأصلية اتقفلت فعليًا)، و(2) اختبار الـ cookie wall
اللي كان اترجّع في المحاولة التانية (regression) رجع PASSED — يعني
`is_navigation_request()` صلّح المشكلتين مع بعض من غير ما يكسر حاجة
تانية. باقي الـ 22 اختبار في نفس الـ run (Byparr/Patchright/باقي
mock targets) كلهم PASSED برضه — regression check كامل، مش بس
الاختبار الجديد. الفجوة دي (بند 4 في obstacle map) مقفولة فعليًا،
بدليل CI حقيقي، مش نظري.

### 10. تصعيد تكامل (مش تصعيد صعوبة) — دمج 4 طبقات قديمة مع بعض في اختبار واحد (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md، "العقبات المركّبة" — النص المذكور صراحة: "دفاع طبقة واحدة سهل تجاوزه")

**الفرق النوعي مهم يتسجّل بوضوح:** كل الجولات اللي فاتت (1-9) كانت
**تصعيد صعوبة** — كل جولة بتضيف طبقة عقبة *جديدة* (honeypots، markup
randomizer، cookie wall، A/B variants، ...). الجولة دي مختلفة: **مفيش
كود إنتاجي جديد ولا طبقة عقبة جديدة اتضافت خالص** — الأربعة طبقات
(honeypots + markup randomizer من Round 1، cookie wall + A/B variants
من الجولات اللي بعدها) كانت أصلاً شغّالة مع بعض بالصدفة، من غير قصد،
لأن `docker-compose.test.yml`'s `ENABLE_*` flags كلها افتراضها `true`
ومفيش جولة قفلت طبقة قديمة عشان تختبر طبقة جديدة لوحدها. يعني الأربعة
كانوا فعليًا نشطين مع بعض في كل CI run من Round 6 وما بعده — بس **مفيش
اختبار واحد كان بيتحقق من دليل مباشر إن الأربعة اتعدّوا/اتجنّبوا مع
بعض في نفس الطلب** — كل الاختبارات كانت بتتأكد من النتيجة المجمّعة
بس (`items > 0`)، مش من كل طبقة على حدة.

**الحل:** اختبار جديد
(`test_mock_target_camoufox_crawl_gets_real_posts_and_never_reaches_a_real_honeypot`
في `tests/integration/test_mock_target_camoufox_live.py`) بيتحقق من
حجم `test-environment/logs/honeypot_triggers.log` قبل وبعد نفس الكراول
اللي بيتأكد إنه رجع `items > 0` — يعني بيثبت في نفس الوقت: (1) الكراول
عدّى Anubis (محور 2)، (2) عدّى cookie wall الحقيقي (محور 6)، (3) نجح
مهما كان A/B variant اللي الطلب ده وقع عليه (محور 6)، (4) مهما كانت
الـ classes اللي markup randomizer اختارها وقتها (محور 3)، **و**(5)
مالمسش أي واحد من الـ 4 honeypot links المخفيين اللي كانوا موجودين في
نفس الصفحة بالظبط (محور 2) — الأربعة أدلة مع بعض، من نفس الطلب، مش
افتراض مبني على نجاح كل طبقة لوحدها في جولات منفصلة.

**ليه Camoufox بس، مش الـ 3 providers:** Byparr وPatchright عندهم
نفس نوع اختبار honeypot أصلاً (`test_mock_target_live.py`،
`test_mock_target_patchright_live.py`) — بس هو تافه بالنسبة ليهم: هما
أصلاً بيتوقفوا عند Anubis (Byparr بيتقفل قبل ما يخلّص التحدي، Patchright
بيترفض explicit deny) فمستحيل يوصلوا لمحتوى حقيقي أصلاً، فمفيش داعي
يوصلوا لـ honeypot. Camoufox هو الوحيد اللي بيوصل لمحتوى حقيقي فعليًا
مع الأربعة طبقات نشطة، فهو الوحيد اللي الاختبار ده بيبقى له معنى حقيقي.

**اتّحقّق محليًا** (ruff/mypy --strict على الملف المعدّل، 189
unit+contract test PASSED — الاختبار الجديد نفسه بيتخطى (skip) محليًا
بسبب غياب `TITAN_BYPARR_URL`، زي كل اختبار حي تاني في المشروع، وده
متوقع ومقصود).

**✅✅✅ اتأكّد فعليًا (CI run [32677298333](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32677298333)) — دليل حقيقي من الـ job logs، مش افتراض:**
`25 passed, 2 warnings in 285.49s (0:04:45)`، صفر FAILED في اللوج كله.
الاختبار الجديد نفسه اتأكّد PASSED صراحة:
```
tests/integration/test_mock_target_camoufox_live.py::test_mock_target_camoufox_crawl_gets_real_posts_and_never_reaches_a_real_honeypot PASSED
```
يعني في نفس الطلب الحقيقي ده: Camoufox عدّى Anubis (Anubis's log نفسه
أظهر `"new challenge issued"` لـ Camoufox's requests بالظبط، ومفيش
`explicit deny` غير لـ Byparr/plain-Scrapy زي المتوقع تمامًا) + عدّى
cookie wall + اتعامل صح مع أي A/B variant/markup randomizer class وقع
عليها الطلب + مالمسش أي واحد من الـ 4 honeypot links المخفيين
(`honeypot_triggers.log` ثابت الحجم قبل وبعد). باقي الـ 24 اختبار
(byparr/patchright's sentinels، JSON API، Test Targets التانية،
test-environment's 81 unit test) كلهم PASSED برضه — regression check
كامل، مش بس الاختبار الجديد. الأربعة طبقات دي مؤكدين شغالين مع بعض
بدليل CI حقيقي، مش افتراض مبني على نجاح كل طبقة لوحدها.

**بعد كده بس:** خطوة تانية (Shadow DOM كطبقة خامسة، جولة تصعيد صعوبة
حقيقية هذه المرة) — مش تبدأ إلا لو الجولة دي اتأكّدت بدليل CI حقيقي،
عشان أي فشل بعدها نعرف يقين إنه بسبب Shadow DOM تحديدًا مش تفاعل غامض
مع أربعة حاجات تانية جديدة مع بعض.

### 11. Shadow DOM كطبقة خامسة — Known Limitation جديدة، حقيقية (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md، محور 3 "البنية الهيكلية"، فوق نفس الأربعة النشطين من بند 10)

**الإضافة:** `structural/shadow_dom.py` في `test-environment/mock-target`
— نص البوستات (index زوجي، 0-based) فاضل زي ما هو في الـ light DOM،
والنص التاني (index فردي) بقى يترندر جوه **shadow root حقيقي** بيتلحق
client-side بس (`element.attachShadow({mode: "open"})`)، مش أي حيلة
`display:none`/hidden زي باقي الطبقات. الـ payload (author/text/likes/
comments) بيتشفّر base64(JSON) في `data-shadow-payload` عشان مفيش أي
نص حقيقي ظاهر في الـ raw HTML خالص لهذول الـ posts، وسكريبت client-side
(`SHADOW_ATTACH_SCRIPT`) بيبنيهم فعليًا في shadow root حقيقي عند تحميل
الصفحة، عبر DOM construction آمن (`textContent`، مش `innerHTML` string
concatenation).

**السبب ده مختلف نوعيًا عن كل الطبقات اللي فاتت:** honeypots/decoy-data
مشكلة *visibility* (المحتوى موجود في نفس الـ HTML string، بس مخفي).
Shadow DOM مشكلة *encapsulation* — حسب DOM spec نفسه، shadow root
اتلحق بـ `attachShadow()` **مش بيتضمّن خالص** لما تعمل serialize
لـ `outerHTML`/`innerHTML` بتاع العنصر المضيف ليه. Playwright's
`page.content()` (اللي `CamoufoxProvider`/`PatchrightProvider` بيرجّعوه)
هو بالظبط `document.documentElement.outerHTML` تحت الغطا، فبيورث نفس
الـ blind spot ده. `GenericSpider.parse()` بيشغّل CSS selector عادي
(Scrapy/parsel) على الـ **string** ده — مفيش live DOM يتعدّى (pierce)
خالص، فالمحتوى ده بيبقى **مستحيل الوصول له معماريًا**، حتى لو Camoufox
بيشغّل متصفح حقيقي كامل وعدّى كل طبقة تانية على نفس الصفحة بالظبط (بند
10). دي أول طبقة في المشروع كله "شغّل متصفح حقيقي" **مش كفاية ليها** —
الفجوة معمارية (GenericSpider أصلاً مابيسألش عن live DOM، بس بيحلل
string)، مش فجوة توقيت/stealth/click زي كل الفجوات اللي اتصلحت قبل
كده (`post_load_wait_ms`/`click_selector`).

**دليل حتمي، قابل للتكرار (مش تقريبي):** `INDEX_PAGE_SIZE` (10) و
`is_shadow_wrapped` (كل index فردي) الاتنين deterministic — يعني عدد
الـ items المتوقع من كراول حقيقي رقم دقيق: 5 بوست حقيقي فاضل في light
DOM (index زوجي) + decoy twin واحد (`structural/decoy_data.py`، دايمًا
light DOM، مش متأثر بالطبقة دي) = **6 بالظبط**، مش الـ 10 بوست الحقيقيين
اللي `/` فعليًا بيولّدهم. اختبار جديد
(`test_mock_target_camoufox_misses_every_shadow_dom_wrapped_post` في
`tests/integration/test_mock_target_shadow_dom_live.py`) بيتأكد من
الرقم ده بالظبط، مش بس "أقل من قبل".

**الحل الحقيقي (لو حبينا نقفلها بعدين) محتاج تغيير معماري فعلي** —
GenericSpider يكسب مسار استخراج يقدر يعدّي الـ shadow DOM (مثلاً
Playwright locators بتسأل الصفحة الحية بدل parsel على `page.content()`)
— مش متنفّذ دلوقتي؛ مهمة الجولة دي توثيق الفجوة بدليل دقيق قابل للتكرار،
زي أسلوب `test_mock_target_live.py`/`test_mock_target_patchright_live.py`
مع فجوات Anubis بتاعتهم.

**اتّحقّق محليًا** قبل الدفع: test-environment's own pytest suite (92
passed، 100% coverage — الطبقة الجديدة كسبت unit tests لـ
`structural/shadow_dom.py` + `test_app.py` + `test_config.py`)، ruff
على `test-environment/` (مفيش mypy --strict عليها — خارج نطاق
`lint.yml`، زي باقي test-environment/mock-target)، وruff/mypy --strict
على `src`/`tests` (مفيش كود src اتغيّر الجولة دي أصلاً — الفجوة موثّقة،
مش متصلحة).

**✅✅✅ اتأكّد فعليًا (CI run [32678444498](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32678444498)) — دليل حقيقي من الـ job logs، مش افتراض:**
`26 passed, 2 warnings in 304.11s (0:05:04)`، صفر FAILED. الاختبار
الجديد نفسه اتأكّد PASSED صراحة:
```
tests/integration/test_mock_target_shadow_dom_live.py::test_mock_target_camoufox_misses_every_shadow_dom_wrapped_post PASSED
```
يعني الرقم الحتمي المتوقع (6 items بالظبط، مش تقريبي) اتأكّد فعليًا في
كراول حقيقي — الفجوة المعمارية دي حقيقية ومؤكدة، مش نظرية. test-environment's
own 92 unit tests كمان PASSED (رقم منفصل عن الـ 26 اللي فوق، step
مختلف في CI). باقي الـ 25 اختبار تاني (byparr/patchright sentinels،
JSON API، الجولة المركّبة من بند 10، Test Targets التانية) كلهم PASSED
برضه — regression check كامل، الطبقة الجديدة مبوّظتش أي حاجة شغالة.

### 12. حل Shadow DOM فعليًا — تعديل معماري في الـ pipeline، من غير أي مكتبة خارجية (طلب المستخدم صراحة، بند 11)

**الفكرة:** بدل ما `GenericSpider` يعتمد بالكامل على `page.content()` +
CSS selectors على الـ HTML الخام، `SpiderConfig` كسب حقل جديد
`extraction_mode: "parsed_html" | "live_dom"` (افتراضي `parsed_html` —
سلوك كل config قديم من غير تغيير). لما `extraction_mode: live_dom`،
الـ provider نفسه (`CamoufoxProvider`/`PatchrightProvider`) بيستخرج
الـ items مباشرة من الصفحة الحية (`page.locator()`) **قبل** ما المتصفح
يتقفل — ده بيستغل خاصية Playwright's المدمجة: locators بتخترق
(pierce) shadow roots المفتوحة (`open`) تلقائيًا، بعكس أي parser
بيشتغل على string (زي Scrapy/parsel) اللي أصلاً مايشوفش محتوى الـ
shadow root خالص لأنه مش موجود في الـ serialized `outerHTML` أساسًا
(بند 11).

**التغييرات (كل واحدة باختبارات جديدة، مفيش تعديل بدون اختبار):**
- `SpiderConfig.extraction_mode` + validator بيرفض التركيبة لو
  `response_format != "html"` أو `antibot_needed=false` أو
  `antibot_provider` مش `camoufox`/`patchright` — رفض عند تحميل الـ
  config، مش تدهور صامت وقت الطلب.
- `AntibotProvider.solve()` كسب باراميتر تالت اختياري
  `extraction_selectors` (best-effort زي `click_selector` بالظبط —
  `ByparrProvider` بيسجّل تحذير واضح ويتجاهله، مش يعطل أو يسقط بصمت،
  لأن `/v1` API مالوش صفحة حية يستعلم منها خالص).
- `Solution.items: list[dict] | None` — `None` يعني "زي ما كان، حلّل
  الـ html بنفسك"؛ list (حتى لو فاضية) يعني "دي النتيجة الحقيقية،
  متعملش parsing تاني".
- وحدة مشتركة جديدة `src/providers/antibot/_live_dom.py`
  (`extract_live_dom_items`) — بتعيد استخدام نفس صيغة الـ field
  selectors القديمة (`::text`/`::attr(name)`) بالظبط، مفيش لغة selectors
  تانية للتارجت يتعلمها. استخدمنا `text_content()` (زي parsel's
  `::text` بالظبط) مش `inner_text()` — الفرق مهم: `inner_text()`
  بيرجع `""` لأي عنصر `display:none` (زي decoy twin's الهيكل،
  `structural/decoy_data.py`) في متصفح حقيقي، حتى لو النص موجود فعليًا؛
  `text_content()` بيرجع النص الحقيقي بغض النظر عن الظهور — نفس سلوك
  الـ parsing القديم بالظبط، عشان قيم الحقول تفضل واحدة بين الوضعين،
  والفرق الوحيد يكون *الوصول* لمحتوى shadow root، مش تغيير في القيم.
- `ByparrMiddleware` بيمرّر `extraction_selectors` للـ provider، وبيحط
  `solution.items` في `request.meta["live_dom_items"]` (لو موجودة) —
  `response.meta` بيكون passthrough لنفس الـ dict.
- `GenericSpider._parse_html` بيتحقق من `response.meta.get("live_dom_items")`
  الأول — لو موجودة (مش None)، بيستخدمها مباشرة بدل ما يعمل
  `response.css()` تاني.
- Config جديد `src/spiders/configs/mock_target_live_dom.yaml` — نفس
  الـ target/selectors بتاعة `mock_target_camoufox.yaml` بالظبط، الفرق
  الوحيد `extraction_mode: live_dom`. **`mock_target_camoufox.yaml`
  اتسيب من غير تغيير عمدًا** — لسه بيوثّق فجوة بند 11 الحقيقية
  (parsed_html بيرجع 6 items بس).

**تصحيح حقيقي لتوقّع الطلب الأصلي (مش تجاهل، توضيح بالدليل):** الطلب
افترض "10 items مش 6" لو الاختراق نجح. بعد تتبّع الـ pipeline بدقة:
`decoy_data.py`'s decoy twin (post index 0's حية، دايمًا light DOM)
بيحمل نفس `data-role="post"` attribute برضه، فأي locator بيخترق shadow
DOM هيلقطه هو كمان — يعني العدد الحتمي الصح هو **11** (10 بوست حقيقي
[5 light + 5 اترجعوا من جوه shadow root] + decoy واحد)، مش 10 — بالظبط
نفس الـ baseline قبل ما Shadow DOM يتضاف أصلاً
(`test_index_renders_posts_decoy_and_honeypots`'s `== 11`). الاختبار
الجديد (`tests/integration/test_mock_target_live_dom_live.py`) بيتحقق
من الـ 11 دي بالظبط، مش من رقم المستخدم التقريبي.

**اتّحقّق محليًا** (ruff/mypy --strict على `src` — نفس مشكلة
`patchright.sync_api` المحلية القديمة غير المرتبطة بالتعديل ده، 224
unit+contract test PASSED، 88.52% coverage؛ test-environment's own 92
unit test PASSED، 100% coverage — الطبقة الجديدة كسبت unit tests لـ
`_live_dom.py` نفسها + كل provider/middleware/config/spider اتلمس).

**✅✅✅ اتأكّد فعليًا (CI run [32680454673](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32680454673)) — دليل حقيقي من الـ job logs، مش افتراض:**
`27 passed, 2 warnings in 300.75s (0:05:00)`، صفر FAILED في اللوج كله.
الاختبارين الحرجين اتأكّدوا PASSED صراحة في نفس الـ run:
```
tests/integration/test_mock_target_live_dom_live.py::test_mock_target_live_dom_recovers_every_shadow_dom_wrapped_post PASSED
tests/integration/test_mock_target_shadow_dom_live.py::test_mock_target_camoufox_misses_every_shadow_dom_wrapped_post PASSED
```
يعني: (1) `extraction_mode: live_dom` فعليًا استرجع الـ 11 item الحتمية
(10 بوست حقيقي + decoy، مش 6) — الرقم اللي اتصحّح فوق (11 مش 10) اتأكّد
بالظبط، مش تخمين؛ و(2) نفس اللحظة، `mock_target_camoufox.yaml` (اللي
اتسيب من غير تعديل عمدًا) لسه بيرجّع 6 بالظبط — يعني الفجوة الموثّقة في
بند 11 لسه موجودة ومؤكدة لمين مايستخدمش `live_dom`، والحل الجديد
مايمسحش الدليل التاريخي، بيضيف عليه. test-environment's own 92 unit
test كمان PASSED. باقي الـ 25 اختبار تاني (byparr/patchright sentinels،
JSON API، الجولة المركّبة من بند 10، Test Targets التانية) كلهم PASSED
برضه — regression check كامل، التعديل المعماري ده مبوّظش أي حاجة
شغالة. Shadow DOM بقى محلول فعليًا (مش بس موثّق كـ Known Limitation) —
لمين يختار `extraction_mode: live_dom` صراحة.

### 13. DOM Virtualization — Known Limitation حقيقية لـ**الاتنين** parsed_html وlive_dom مع بعض (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md، محور 3، طلب المستخدم صراحة بعد بند 12)

**الفكرة:** `/feed` دلوقتي بيحاكي virtualized list حقيقية — بس N بوست
(افتراضي 5، `DOM_VIRTUALIZATION_WINDOW_SIZE`) موجودين فعليًا في الـ DOM
في أي لحظة. مع كل batch جديد بيتحمّل من `/api/feed`، أقدم البوستات
المعروضة بتتشال فعليًا من الـ DOM (`container.removeChild` — إزالة
حقيقية، مش `display:none`) — نفس الآلية اللي React Window ومنصات
social feed حقيقية بتستخدمها. القاعدة موثّقة ومُختبرة في Python
(`structural/dom_virtualization.py`'s `excess_count`)، ومنعكسة يدويًا
في الـ client-side script (`templates/feed.html`).

**فجوة تمهيدية حقيقية اتكشفت في الطريق، مش جزء من سؤال الـ
virtualization نفسه:** لا `CamoufoxProvider` ولا `PatchrightProvider`
كان عندهم أي قدرة scroll خالص قبل الجولة دي — `PlaywrightMiddleware`'s
scroll loop (موجود من Phase 2) **مستحيل معماريًا يوصله** لأي target
وراء Anubis (`antibot_needed: true`): `ByparrMiddleware` بيرجّع
response الأول، وScrapy بيوقف عن نداء `process_request` لباقي الـ
middlewares بمجرد ما وحدة ترجّع response. يعني `/feed`'s المحتوى
الديناميكي (بند 2.4) **ما كانش أصلاً قابل للوصول** عبر أي من الـ 3
providers طول الوقت. الحل: وحدة مشتركة جديدة
`src/providers/antibot/_scroll.py` (`scroll_to_load_lazy_content`) —
نفس منطق `playwright_middleware.py`'s `_scroll_to_load_lazy_content`
بالظبط، متلحقة الآن في `_default_camoufox_solve`/`_default_patchright_solve`
(بعد `post_load_wait_ms`، عشان المحتوى الحقيقي يكون وصل الأول قبل ما
نحاول نعمل scroll له). قيم ثابتة (8 attempts، 700ms pause) زي
`PlaywrightMiddleware`'s الافتراضية بالظبط — مفيش constructor param
جديد (YAGNI، مفيش حاجة حقيقية دلوقتي تحتاج تعديل per-target).

**السؤال الحقيقي المفتوح:** بعد ما بقى فيه scroll حقيقي، هل
`extraction_mode: live_dom` (بند 12's الحل لـ Shadow DOM) يقدر يمسك
البوستات اللي "اتشالت" فعليًا من الـ DOM؟ **الفرضية (من المستخدم
نفسه): لأ — المشكلتين مختلفتين في النوع.** Shadow DOM مشكلة
*encapsulation* (المحتوى موجود في live DOM، بس متغطّي عن string
parser). Virtualization مشكلة *وجود فعلي + توقيت*: البوست اللي
اتشال حقيقي مش موجود خالص في أي لحظة قراءة — سواء `page.content()`
(string) أو `page.locator()` (live query)، الاتنين بيقروا حالة الـ
DOM **في نفس اللحظة** (بعد ما الـ scroll loop يخلص)، فمفيش حيلة
استخراج تقدر تسترجع حاجة مش موجودة أصلاً وقت القراءة.

**Config جديدين للتجربة (نفس target/selectors، extraction_mode بس
مختلف):** `mock_target_feed_virtualized_parsed_html.yaml` و
`mock_target_feed_virtualized_live_dom.yaml` — الاتنين بيستهدفوا
`/feed` عبر camoufox. **التوقع الحتمي، مش تقريبي:** بما إن
`FEED_PAGE_SIZE` (10) أكبر من `DOM_VIRTUALIZATION_WINDOW_SIZE` (5)،
أول batch لوحده كفاية إن الـ trim يوصل بالضبط لـ 5 — يعني `len(items)
== 5` متوقعة من الاتنين، بغض النظر عن عدد دورات الـ scroll الفعلية.

**اتّحقّق محليًا** قبل الدفع: test-environment's own pytest suite
(101 passed، 100% coverage — طبقة جديدة `dom_virtualization.py` +
`_scroll.py` كسبوا unit tests كاملة)، ruff على `test-environment/`،
230 unit+contract test PASSED على `src` (88.64% coverage)، ruff/mypy
--strict على `src`/`tests` (نفس مشكلة `patchright.sync_api` المحلية
القديمة غير المرتبطة).

**✅✅✅ الفرضية اتأكّدت فعليًا (CI run [32725098955](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32725098955)) — دليل حقيقي من الـ job logs، مش افتراض:**
`29 passed, 2 warnings in 332.58s (0:05:32)`، صفر FAILED. الاختبارين
الحاسمين اتأكّدوا PASSED صراحة في نفس الـ run:
```
tests/integration/test_mock_target_dom_virtualization_live.py::test_parsed_html_only_recovers_the_final_virtualization_window PASSED
tests/integration/test_mock_target_dom_virtualization_live.py::test_live_dom_also_only_recovers_the_final_virtualization_window PASSED
```
يعني الرقم الحتمي المتوقع (5 items بالظبط) اتأكّد فعليًا من **الاتنين**
— `extraction_mode: live_dom` (اللي حل Shadow DOM فعليًا في بند 12)
**ماقدرش** يسترجع البوستات المُزالة من virtualization، بالظبط زي
الفرضية. السبب مؤكد فعليًا مش نظري: الاتنين (`page.content()` كـ
string، و`page.locator()` كـ live query) بيقروا حالة الـ DOM في **نفس
اللحظة** — بعد ما الـ scroll loop يخلص — فمفيش استخراج ممكن يسترجع
بوست مش موجود خالص في الـ DOM وقت القراءة، بعكس Shadow DOM اللي كان
المحتوى موجود فعليًا (بس متغطّي). دي أول جولة في المشروع كله بيبان
فيها بوضوح إن `live_dom` **مش حل عام لكل مشكلة DOM** — بيحل مشاكل
*encapsulation* بس، مش مشاكل *وجود/توقيت*. باقي الـ 27 اختبار تاني (بند
9/10/11/12's tests، byparr/patchright sentinels، Test Targets التانية)
كلهم PASSED برضه — regression check كامل، إضافة الـ scroll capability
الجديدة مبوّظتش أي حاجة شغالة (ولا حتى honeypot اتلمس بالغلط من الـ
scrolling). test-environment's own 101 unit test كمان PASSED. الفجوة
دي (بند 3 obstacle map) موثّقة رسميًا كـ Known Limitation حقيقية
ومؤكدة لـ**الاتنين**، مش قابلة للحل بدون تغيير معماري أعمق (جمع الـ
items تدريجيًا أثناء كل دورة scroll بدل قراءة واحدة نهائية — خارج نطاق
الجولة دي عن قصد).

### 14. حل DOM Virtualization فعليًا عبر Progressive Scroll + Incremental Extraction (طلب المستخدم صراحة بعد بند 13 — التغيير المعماري اللي بند 13 قال إنه خارج نطاقه)

**المحاولة الأولى (من أحدث الممارسات الموثّقة اللي المستخدم بحث فيها
قبل الطلب): Progressive scroll + incremental extraction.** الفرق عن
بند 13's القراءة الواحدة النهائية: بدل ما نستنى الـ scroll loop يخلص
وبعدين نقرا الـ DOM مرة واحدة، بنستخرج/نلقّط snapshot بعد **كل** خطوة
scroll لوحدها — قبل ما eviction يشيل اللي معروض دلوقتي. الـ
deduplication بـ`post_id` كمفتاح (مش أي حقل تاني قابل للتكرار)، وبـ
dict/set واحد شامل الـ crawl **كله** من أول التشغيل، مش بس داخل كل
خطوة لوحدها — عشان بوست شافناه في نافذة مبكرة (قبل ما يتشال) يفضل
موجود حتى لو اتشال بعد كده، وبوست لسه ظاهر في أكتر من قراءة ميتسجّلش
مرتين.

**البنية (مطبّقة على الاتنين extraction_mode، بدون أي تغيير في السلوك
الافتراضي — `progressive_extraction: bool = False` جديد في
`SpiderConfig`، الاتنين configs القديمة من بند 13 فضلوا زي ما هم بالظبط
كـ regression sentinel):**
- `src/providers/antibot/_scroll.py`: `scroll_and_collect` (نسخة من
  `scroll_to_load_lazy_content` بتاخد `collect_fn` وتناديها بعد كل
  قراءة، بما فيها القراءة الأولى قبل أي scroll) + `collect_html_snapshots`
  (بتلقّط `page.content()` بعد كل خطوة، بترجع كل الـ snapshots كـ list).
  `scroll_to_load_lazy_content` نفسها فضلت **من غير أي تعديل** — صفر
  مخاطرة على الكولرز الحاليين المُثبّتين.
- `src/providers/antibot/_live_dom.py`: `collect_live_dom_items_progressively`
  — بتعيد query الـ live DOM بعد كل خطوة scroll (عبر `scroll_and_collect`)،
  وبتجمّع النتايج في dict مفتاحه `post_id`، شامل الـ crawl كله.
- `src/providers/antibot/parsed_html.py` (وحدة جديدة): `extract_parsed_html_items`
  — بتاخد HTML string خام (مش Scrapy Response) وتستخرج منه العناصر عبر
  `parsel.Selector` مباشرة (نفس محرك `response.css()` الداخلي بالظبط)،
  لأن الـ provider layer معاه strings خام بس مش Response objects.
  `generic_spider.py` هو اللي بيستدعيها لكل snapshot ويعمل merge/dedupe
  بـ`post_id` بنفسه، لأنه هو بس اللي عارف إيه الحقل الحقيقي للـ identity.
- `AntibotProvider.solve()` كسب parameter اختياري جديد
  `progressive_extraction: bool = False`. Byparr (مالوش صفحة حية
  يعمل عليها scroll، الـ `/v1` API بيرجّع HTML بس) بيتجاهله best-effort
  مع warning log — نفس نمط `click_selector`/`extraction_selectors`
  الموجود قبل كده. `SpiderConfig`'s validator بيمنع الكومبنيشن
  المستحيلة (byparr + progressive_extraction) من الأساس، كـ
  defense-in-depth.
- **Bug حقيقي اتكشف واتصلح في الطريق:** أول تنفيذ inline جوه
  `_default_camoufox_solve`/`_default_patchright_solve` (اللي مستحيل
  تتعمله unit test مباشر — محتاج متصفح حقيقي) خلّى `camoufox_provider.py`
  يهبط لـ 38% coverage و`patchright_provider.py` لـ 37% — كسر
  `--cov-fail-under=85` فعليًا (84.23% total). الحل: استخراج المنطق
  لدوال مستقلة قابلة للاختبار (`collect_live_dom_items_progressively`،
  `collect_html_snapshots`) بـ fake Page/Locator objects (`_FakeVirtualizedPage`
  جديدة في `test_live_dom.py` بتحاكي محتوى الـ DOM بيتغيّر بين قراءتين
  — نفس فكرة eviction الحقيقية)، بدل ما تتسيب inline جوه دوال مش
  مُختبَرة مباشرة. رجّع الـ coverage لـ86.07% (اتأكّد محليًا).

**Configs جديدة للتجربة (بند 13's configs فضلوا زي ما هم، من غير أي
تعديل):** `mock_target_feed_virtualized_progressive_parsed_html.yaml`
و`mock_target_feed_virtualized_progressive_live_dom.yaml` — نفس
target/selectors، `progressive_extraction: true` بس هو الفرق.

**التوقع (مبني على قراءة الكود الفعلي، مش رقم عشوائي — التفاصيل في
`tests/integration/test_mock_target_dom_virtualization_progressive_live.py`'s
docstring): 10 items بالظبط، مش 5.** `FEED_PAGE_SIZE` (10) >
`DOM_VIRTUALIZATION_WINDOW_SIZE` (5)، فأول batch لوحده بيتقص لـ5 قبل
حتى أول قراءة progressive (بعد `post_load_wait_ms`). خطوة scroll واحدة
بس بتحصل فعليًا (لأن حجم الـ DOM بيفضل شبه ثابت بعد أول trim، فـ
`scrollHeight` بيوقف يكبر) — يعني نافذتين منفصلتين بـ5 بوستات لكل
واحدة، **مش متداخلين** (post_ids مختلفة) = 10 اتنين مجمّعين.

**اتّحقّق محليًا** قبل الدفع: `ruff check src/ tests/` نظيف، `mypy
--strict src` نظيف (عبر `python -m mypy`، نفس بيئة CI الحقيقية —
تفاصيل ليه `mypy` binary العام بيفشل محليًا بس `python -m mypy` بينجح
موثّقة في الـ commit نفسه)، 268 unit+contract+integration test PASSED
محليًا (86.56% coverage، الـ 7 فشل الوحيدين في نفس الـ run بتوع
مواقع خارجية حقيقية زي quotes.toscrape.com — sandbox بلا إنترنت خارجي،
مش علاقة بالتغيير)، test-environment's own suite 101 passed (100%
coverage، بدون أي تعديل هناك أصلاً هذه الجولة). **الاختبارين الحاسمين
(`test_progressive_parsed_html_recovers_both_virtualization_windows`،
`test_progressive_live_dom_recovers_both_virtualization_windows`)
بيتخطّوا محليًا** (مفيش `TITAN_BYPARR_URL` — مفيش live network stack
محليًا)، **لسه محتاجين تأكيد CI حقيقي** — النتيجة الفعلية (مش
الافتراض) هتتسجّل هنا بمجرد ما الـ run يخلص، زي كل جولة قبل كده.

**❌ المحاولة الأولى (توقع 10) فشلت فعليًا (CI run
[32730994089](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32730994089))
— دليل حقيقي من الـ job logs، مش افتراض:** `3 failed, 28 passed`.
الاختبارين الحاسمين فشلوا الاتنين بنفس القيمة:
```
FAILED tests/integration/test_mock_target_dom_virtualization_progressive_live.py::test_progressive_parsed_html_recovers_both_virtualization_windows - AssertionError: expected exactly 10 items ... got 5
FAILED tests/integration/test_mock_target_dom_virtualization_progressive_live.py::test_progressive_live_dom_recovers_both_virtualization_windows - AssertionError: expected exactly 10 items ... got 5
```
**السبب الجذري اتأكّد من نفس الـ run's structured logs، مش تخمين:**
`camoufox_provider.solved`'s own log line أظهر `html_snapshot_count: 2`
(parsed_html) و`live_dom_item_count: 5` (live_dom) — يعني آلية الجمع
نفسها اشتغلت بالظبط زي ما اتصممت (قرايتين حصلوا فعليًا، مش قرايه
واحدة)، بس القرايتين الاتنين لقطوا **نفس** الـ 5-post window، مش
نافذتين مختلفتين. السبب: `scroll_and_collect` كان لسه وارث نفس
heuristic بتاع `scroll_to_load_lazy_content` ("قف لو `scrollHeight`
بطل يكبر") — heuristic **غلط** لهدف virtualized: الارتفاع المُصيّر
بيفضل شبه ثابت بين كل خطوة (الـ eviction بيحافظ عليه عند حجم الـ
window تقريبًا)، بغض النظر عن قد ايه محتوى جديد فعلاً اتحمّل — فالـ
loop كان بيوقف بعد أول محاولة scroll واحدة بس، دايمًا، مهما كان
`max_attempts`.

**الحل (نفس المحاولة الأولى، مراجعة — مش تحول لمحاولة تانية):**
`scroll_and_collect` (`_scroll.py`) اتعدّل عشان (1) يشيل الاعتماد على
`scrollHeight` خالص — يعمل بالظبط `max_attempts` دورة scroll+collect،
من غير أي early exit، و(2) يبعت `window.dispatchEvent(new
Event('scroll'))` صراحة جنب `scrollTo()` — اكتشاف حقيقي تاني من نفس
التحقيق: `scrollTo()` لوحدها مش بتبعت 'scroll' event حقيقي لو المحتوى
المُصيّر بقى قصير كفاية إنه يتلم في الـ viewport (وده بالظبط اللي
بيحصل بعد أول trim)، و`templates/feed.html`'s الـ `loadMore()` trigger
مربوط بـ'scroll' event ده تحديدًا — فمن غيره ما كانش هيتنادى تاني
خالص. `scroll_to_load_lazy_content` نفسها فضلت **من غير أي تعديل**.

**التوقع اتراجع فعليًا بعد إعادة اشتقاق كاملة (25 مش 10):** بتتبّع
`templates/feed.html`'s trim rule عبر كل الـ 5 صفحات (`MAX_FEED_PAGES`)
— كل batch جديد (10 بوست) بيتحط فوق الـ 5 remainder المتبقي من قبل
(5+10=15)، والـ trim بيشيل الـ remainder القديم كله + أول 5 من الـ
batch الجديد، فمايفضلش غير آخر 5 بتوع كل صفحة. يعني كل صفحة من الـ 5
بتساهم بـ5 بوست بس (مش 10) — 5×5 = 25 بوست فريد بالظبط، مش 50 ومش 10.
التفاصيل الكاملة والاشتقاق موثّق في
`tests/integration/test_mock_target_dom_virtualization_progressive_live.py`'s
docstring نفسه (بما فيه الغلطة الأولى، موثّقة برضه مش ماسوحة).

**اتّحقّق محليًا** بعد المراجعة: `ruff check` نظيف، `mypy --strict`
نظيف، unit tests جديدة بتأكّد الـ contract الجديد (`max_attempts` هو
الشرط الوحيد للتوقف، الـ dispatch الاصطناعي بيتبعت كل مرة) — بما فيها
اختبار جديد بيثبّت إن الجمع بيستمر عبر أكتر من محاولتين، مش بس اتنين،
عشان بالظبط الـ bug القديم كان بيوقف بعد واحدة بس. 262 unit+contract
test PASSED (86.02% coverage، لسه فوق الـ gate). **لسه محتاجين تأكيد CI
حقيقي تاني** للرقم الجديد (25) — النتيجة الفعلية هتتسجّل هنا بمجرد ما
الـ run التاني يخلص.

**ملاحظة صادقة، مش متجاهلة:** نفس run 32730994089 فيه فشل تالت مش
متعلق — `test_mock_target_live_dom_recovers_every_shadow_dom_wrapped_post`
(بند 12، مستهدف `/`) فشل بـ`Page.click: Timeout 30000ms exceeded ...
waiting for locator("#accept-cookies")`. الـ diff بتاع الجولة دي ملموش
أي حاجة في click_selector handling ولا cookie wall ولا shadow_dom.py —
محتمل يكون infra flake (ضغط موارد حقيقي من إضافة 2 اختبار browser تقيل
جديدين للـ run نفسه)، بس مش مؤكد فعليًا لسه. هيتراقب في الـ run الجاي؛
لو اتكرر، هيتحقق فيه بجدية كـ regression حقيقي مش flake.

**❌ push المراجعة نفسه (commit 98c725b) فشل في أول محاولة — CI run
[32733064348](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32733064348)،
بس السبب مختلف تمامًا هذه المرة، مش في الكود خالص:** `python -m
camoufox fetch` (خطوة تجهيز المتصفح في الـ workflow نفسه) فشلت بـ`504
Server Error: Gateway Timeout` من `api.github.com` (3 مرات متتالية)،
والخطوة كمّلت بصمت من غير متصفح متثبّت — كل اختبار بيستخدم Camoufox فشل
بـ0 items، **بما فيها اختبارات بند 13 القديمة اللي شغّالة وناجحة من كذا
جولة** (9 failed, 22 passed) — دليل واضح إنها infra flake حقيقية، مش
regression. عملت `rerun_failed_jobs` (مش push جديد) على نفس الـ run
عشان أتأكد.

**المحاولة التانية لنفس الـ run** (`run_attempt=2`) أثبتت إن خطوة
تجهيز Camoufox نجحت هذه المرة (مفيش 504 تاني)، ورجّعت نتيجة حقيقية
جديدة تمامًا: `2 failed, 29 passed`.
- `test_progressive_live_dom_recovers_every_virtualization_window`
  **PASSED فعليًا بـ25 بالظبط** — التوقع المُعاد اشتقاقه اتأكّد!
- `test_mock_target_live_dom_recovers_every_shadow_dom_wrapped_post`
  (بند 12) **PASSED** — يعني الفشل بتاعه في الـ run الأول كان فعلاً
  flake مش regression، زي ما اتوقعنا.
- `test_progressive_parsed_html_recovers_every_virtualization_window`
  **فشل بـ20 بدل 25** — قريب جدًا، مش صفر. `html_snapshot_count: 9`
  (كل الـ8 محاولات اشتغلت، مفيش early exit — إصلاح المراجعة التانية
  فضل شغّال صح). السبب الجذري الحقيقي: race condition حقيقي —
  `templates/feed.html`'s الـ `loading` flag بيسقط نداء `loadMore()`
  لو الـ fetch اللي قبله لسه شغّال، و`DEFAULT_SCROLL_PAUSE_MS` (700ms،
  متظبّط لهدف lazy-load عادي أخف) مش دايمًا كافي لجولة fetch+trim كاملة
  تحت ظروف شبكة CI حقيقية أحيانًا مزدحمة.
- `test_live_dom_also_only_recovers_the_final_virtualization_window`
  (بند 13 القديم) فشل بـ0 بسبب `Page.wait_for_timeout: Target page,
  context or browser has been closed` — عطل متصفح حقيقي مختلف تمامًا
  عن أي فشل سابق لنفس الاختبار (3 أسباب مختلفة عبر 3 runs: click
  timeout، binary مش متثبّت، متصفح اتقفل) — نمط بيرجّح ضغط موارد CI،
  مش regression حقيقي، خصوصًا إن الـ diff مالوش أي علاقة بالكود ده.

**الحل (مراجعة تالتة، لسه نفس المحاولة الأولى):** ثوابت مخصّصة أسخى
للـ progressive path بس (`DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS=10`،
`DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS=1500ms`) في الاتنين
camoufox/patchright providers — الثوابت المشتركة القديمة
(`DEFAULT_MAX_SCROLL_ATTEMPTS`/`DEFAULT_SCROLL_PAUSE_MS`) اللي بيستخدمها
`scroll_to_load_lazy_content` (كل الـ callers التانية المُثبّتة) فضلت
**من غير أي تعديل**. اتحقّق محليًا: ruff/mypy نظيفين، 262 test PASSED
(86.05% coverage). **لسه محتاجين تأكيد CI حقيقي تالت** للرقم 25 مع
الثوابت الجديدة — النتيجة الفعلية هتتسجّل هنا بمجرد ما الـ run يخلص.

**✅✅✅ اتأكّدت فعليًا (CI run
[32735734451](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32735734451))
— دليل حقيقي من الـ job logs، مش افتراض:** `31 passed, 0 failed` في
`392.62s (0:06:32)`، صفر FAILED. الاختبارين الحاسمين اتأكّدوا PASSED
صراحة في نفس الـ run:
```
tests/integration/test_mock_target_dom_virtualization_progressive_live.py::test_progressive_parsed_html_recovers_every_virtualization_window PASSED
tests/integration/test_mock_target_dom_virtualization_progressive_live.py::test_progressive_live_dom_recovers_every_virtualization_window PASSED
```
يعني الرقم الحتمي المُعاد اشتقاقه (25 بالظبط) اتأكّد فعليًا من
**الاتنين** extraction_mode. بند 13's sentinels القديمة (نفس الـ run)
لسه PASSED برضه بالرقم القديم (5 بالظبط، من غير أي تغيير)، و
`test_mock_target_live_dom_recovers_every_shadow_dom_wrapped_post`
(بند 12) PASSED برضه — يعني فشله في الـ 3 runs السابقة كان فعلاً
CI-resource-contention flake، مش regression حقيقي، زي ما اتوقعنا.

**الخلاصة الصادقة الكاملة لبند 14 (3 محاولات موثّقة، زي نمط JSON
parsing بالظبط — مفيش حاجة اتمسحت):**
1. المحاولة الأولى (توقع 10) فشلت فعليًا (CI run 32730994089).
2. المراجعة الأولى (شيل heuristic الـ scrollHeight الغلط + dispatch
   event صريح، توقع مُعاد اشتقاقه 25) نجحت جزئيًا فقط (CI run
   32733064348: `live_dom`=25 ✅، `parsed_html`=20 ❌) بعد ما نفس الـ
   run نفسه اتأكّد فيه infra flake غير متعلقة تمامًا (504 من GitHub
   API لـ camoufox fetch) في المحاولة الأولى لنفس الـ commit.
3. المراجعة التانية (ثوابت أسخى مخصّصة للـ progressive path بس)
   نجحت بالكامل (CI run 32735734451: 25/25 للاتنين).

**النتيجة النهائية:** DOM Virtualization (بند 13 obstacle map، محور 3)
محلولة معماريًا بالكامل عبر progressive scroll + incremental
extraction، مؤكدة بدليل CI حقيقي لـ**الاتنين** extraction_mode. الفجوة
الأصلية (بند 13's configs) لسه موثّقة ومش ممسوحة — regression sentinel
دائم يوضّح الفرق بين القراءة النهائية الواحدة والتجميع التدريجي.
`docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md` اتحدّث ليعكس الحل.

### 15. Login/Session — POST + CSRF + session persistence + session-expiry detection (محور 5، Known Limitation #1، طلب المستخدم صراحة قبل أي بند تاني من الجدول)

**الفكرة:** صفحة `/login` حقيقية على mock-target (username/password
ثابتين للاختبار)، فورم فيه CSRF token عشوائي بيتغيّر كل تحميل (single-use
— أي محاولة إعادة استخدامه بترفض)، وPOST بيرجّع session cookie حقيقي
(TTL حقيقي، `security/auth.py`'s `SessionStore`) لو البيانات والتوكن
صح. مسار جديد منفصل تمامًا `/feed-protected` (نفس بيانات `/feed`، عبر
`content_generator.generate_feed_page` نفسها) — `/` و`/feed` الأصليين
اتسابوا من غير أي تعديل (regression sentinel زي كل مرة). من غير session
صالح: 401 صريح (مش redirect لـ `/login`، طلب المستخدم صراحة).

**فجوة معمارية حقيقية اتكشفت في الطريق، قبل حتى كتابة أول سطر كود
تنفيذي:** كل route على mock-target (بما فيها `/login` و`/feed-protected`
الجديدين) وراء Anubis — اتأكّد مباشرة من `anubis/botPolicy.yaml` نفسه
إن مفيش أي استثناء بناءً على الـ path. يعني مستحيل معماريًا POST مباشر
(HTTP عادي، من غير متصفح) يوصل لـ `/login` أصلاً — لازم متصفح حقيقي
(camoufox/patchright) يعدّي تحدي Anubis الأول. **فجوة معمارية تانية
أعمق:** كل نداء `AntibotProvider.solve()` بيشغّل متصفح جديد تمامًا
ويقفله بعد ما يخلص (اتأكّد من قراءة الكود نفسه: `with Camoufox(...) as
browser` جوّه `_default_camoufox_solve`) — يعني الكوكيز **مبتفضلش**
بين نداءين solve() منفصلين. الحل: كل رحلة login + (اختباري) session
expiry probe + الوصول للـ target الحقيقي بتحصل **جوّه نداء solve()
واحد بس**، نفس براوزر واحد — مش عبر Scrapy requests منفصلة بتشارك
cookie jar (ده كان محتاج تغيير معماري أكبر بكتير، خارج نطاق الجولة دي
عن قصد).

**الكود المُضاف:**
- `test-environment/mock-target/security/auth.py`: `CsrfTokenStore`
  (single-use)، `SessionStore` (TTL حقيقي + injectable clock زي
  `FeedRateLimiter` بالظبط، + `force_expire` اختباري بس)،
  `check_credentials` (`hmac.compare_digest`، مش `==`).
- 3 routes جديدة في `app.py`: `GET/POST /login`، `GET /feed-protected`
  (401 صريح من غير session، pagination بسيطة `?page=N`)،
  `GET /test-expire-session` (instrumentation اختباري بس، زي
  `/honeypot-trap/<token>` بالظبط — بيخلّي اختبار session expiry
  حتمي من غير أي انتظار حقيقي فيه flakiness).
- `src/core/interfaces/antibot_provider.py`: `LoginFlow` model جديد +
  `solve()` كسب `login_flow` (best-effort، نفس نمط `click_selector`).
- `src/providers/antibot/_login.py` (وحدة جديدة): `submit_login_form`
  (fill+click حقيقي — الـ CSRF token بيتبعت تلقائيًا كـ hidden field
  عادي، من غير أي parsing/إعادة بناء يدوي من طرفنا) و
  `perform_login_and_navigate` (orchestration قابل للاختبار منفصل —
  استخراج ضروري عشان الـ coverage gate، زي بند 14's بالظبط).
- `camoufox_provider.py`/`patchright_provider.py`: login بيحصل **قبل**
  أي navigation تاني، بالاعتماد على نفس آلية تتبّع
  `last_main_frame_response` الموجودة أصلاً — نجاح/فشل بيتحدد من
  status code الاستجابة الحقيقية، مش افتراض.
- `byparr_provider.py`: best-effort warning + تجاهل (مفيش قدرة
  form-fill/interact خالص في `/v1` API).
- `GenericSpider`: `_request_meta()` بيمرّر `login_flow`، و**بيفعّل
  `handle_httpstatus_list: [401, 403]` بشكل غير مشروط** (مش بس لما
  `login` متظبط) — فجوة حقيقية اتكشفت: Scrapy's `HttpErrorMiddleware`
  (spider middleware افتراضي) بيرمي أي استجابة non-2xx **قبل** ما
  توصل لـ `parse()` خالص من غير الـ opt-in ده — يعني لو سبنا الشرط
  مربوط بـ `login` بس، سيناريو "target محمي من غير login متظبط خالص"
  (الاختبار السلبي المطلوب) كان هيفشل بصمت تام، مش هيتسجّل أي حاجة.
  التفعيل غير المشروط آمن لكل target تاني بردو: Anubis نفسه بيرجّع
  200 دايمًا لصفحات التحدي/الرفض (`botPolicy.yaml`'s
  `status_codes: CHALLENGE: 200, DENY: 200`) فمفيش تقاطع خالص.
  `_parse_html()` كسب فحص مبكر: `response.status in (401, 403)` →
  log واضح (`generic_spider.protected_target_rejected`) + return،
  مش crash ومش فشل صامت.
- `SpiderConfig.login: LoginConfig | None` + validator (نفس نمط
  `extraction_mode`/`progressive_extraction`: `antibot_needed: true` +
  provider حقيقي بس).

**3 configs جديدة:** `mock_target_login_protected.yaml` (المسار
الناجح كامل)، `mock_target_login_protected_session_expiry.yaml` (نفسه
+ `session_expiry_probe_url` — سيناريو اختباري حتمي، مش انتظار حقيقي
فيه flakiness)، `mock_target_feed_protected_no_login.yaml` (السيناريو
السلبي: بدون `login` خالص).

**اتّحقّق محليًا** قبل الدفع: ruff/mypy --strict نظيفين، 289
unit+contract test PASSED (85.25% coverage، مش بعيد عن الحد لكن فوقه
بأمان)، test-environment's own suite 137 test PASSED (100% coverage —
23 اختبار جديد لـ`security/auth.py` + 13 اختبار route-level في
`test_app.py`). **لسه محتاجين تأكيد CI حقيقي** للاختبارات الحية الـ3
الجديدة (`test_login_flow_reaches_protected_data_after_a_real_post_and_csrf_token`،
`test_feed_protected_without_any_login_yields_nothing_not_a_crash`،
`test_session_expired_mid_crawl_after_a_real_login_yields_nothing_not_a_crash`)
— النتيجة الفعلية هتتسجّل هنا بمجرد ما الـ run يخلص.

**❌ الـ push الأول فشل فعليًا (CI run
[32771322702](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32771322702))
— بس مش في الاختبارات الحية، في خطوة تانية قبلهم خالص:** `Unit tests
(coverage gate >= 85%)` — `260 passed` بس `Total coverage: 85.00%`، فشل
الـ gate. **السبب الجذري الحقيقي، اتأكّد بإعادة نفس أمر الـ CI بالظبط
محليًا (`pytest tests/unit --cov=src --cov-fail-under=85`، مش
`tests/unit tests/contract` زي ما كنت بتحقق منه محليًا طول الوقت):**
الـ CI workflow (`.github/workflows/ci.yml`) بيشغّل الـ coverage gate
على `tests/unit` **بس**، منفصل تمامًا عن `tests/contract` (خطوة تانية
براها، من غير `--cov`) — يعني كل تحقّقاتي المحلية طول الجولة دي كانت
بتحسب `tests/unit + tests/contract` مع بعض (رقم أعلى بشكل مصطنع، لأن
اختبار العقد الجديد لـ`login_flow` بيغطّي كود حقيقي في الـ3 providers
مش بيتغطّى من `tests/unit` لوحدها) — فجوة حقيقية في عملية التحقّق
بتاعتي نفسها، مش بس في الكود. الرقم الحقيقي (unit-only) كان 85.00%
بالظبط — عند الحد تمامًا، وده كفاية إنه يفشل (لازم يكون *فوق* 85% مش
مساوي بالظبط، حسب دقة الفاصلة العشرية الداخلية).

**الحل:** استخراج المنطق المتبقي جوّه `_default_camoufox_solve`/
`_default_patchright_solve` (حساب `final_status` + قرار أي log event
ينده) لدالة جديدة قابلة للاختبار `log_login_outcome` في `_login.py` —
`perform_login_and_navigate` بقت بترجّع `(login_ok, final_status)` معًا
(بدل `bool` بس)، فمعظم منطق القرار بقى برّه الدالة اللي مستحيل تتغطّى
مباشرة. النتيجة (unit-only، نفس أمر CI بالظبط): **86.08%** — هامش أمان
حقيقي، مش عند الحد. 264 test PASSED. اتأكّد كمان: `tests/contract`
لوحدها 29 test PASSED، test-environment's own suite 137 test PASSED
(100%). **الدرس المسجّل صراحة:** أي تحقّق محلي جاي لازم يستخدم بالظبط
نفس أوامر `.github/workflows/ci.yml` (منفصلة، مش مجمّعة)، مش تركيبة
مريحة بس مختلفة عن الواقع.

**✅✅ مُختبر ومحلول فعليًا بدليل CI حقيقي — CI run
[32785461995](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32785461995)
(commit `e49c745`)، Lint run
[32785461981](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32785461981):**
كل الخطوات نجحت فعليًا — `Unit tests (coverage gate >= 85%)`: 264
test PASSED، 86.08% coverage (نفس الرقم اللي اتأكّد منه محليًا فوق).
`Contract tests`: 29 test PASSED. `test-environment unit tests`: 137
test PASSED، 100% coverage. **الاختبارات الحية الـ3 الجديدة نجحت
بالاسم فعليًا:**
`test_login_flow_reaches_protected_data_after_a_real_post_and_csrf_token`
PASSED (5 items حقيقية بعد تسجيل دخول ناجح فعلي: GET /login → parse
CSRF → POST → session cookie → /feed-protected)،
`test_feed_protected_without_any_login_yields_nothing_not_a_crash`
PASSED (0 items، الرفض 401/403 اتسجّل في اللوج مش crash)،
`test_session_expired_mid_crawl_after_a_real_login_yields_nothing_not_a_crash`
PASSED (0 items، `session_expired_mid_crawl` اتسجّل في اللوج فعليًا).

**ملاحظة صادقة (مش هتتمسح، append فقط):** أول محاولة تشغيل لنفس الـ
commit (`attempt 1` من نفس الـ run) فشلت فعليًا — بس في اختبار قديم مش
له علاقة بالجولة دي خالص:
`tests/integration/test_mock_target_live_dom_live.py::test_mock_target_live_dom_recovers_every_shadow_dom_wrapped_post`
(بند 12، Shadow DOM، مبيستخدمش `login_flow` خالص) فشل بخطأ Playwright
حقيقي `Page.click: Target crashed` أثناء انتظار `#accept-cookies` —
كراش حقيقي في عملية المتصفح (camoufox)، مش خطأ منطقي في الكود. اتأكّد
إنه flake بإعادة تشغيل الـ job الفاشل بس (`rerun_failed_jobs`) مرة
واحدة — نجح فعليًا في المحاولة التانية (`attempt 2`، نفس الـ commit
`e49c745`)، فده يأكّد إنه مش مرتبط بالتغييرات دي (مش بـ`login_flow`،
ومش بـ`handle_httpstatus_list` اللي بقى unconditional — الاختبار ده
بيستهدف `/` مش أي مسار محمي).

**الخلاصة:** بند Login/Session (POST + CSRF + session persistence +
اكتشاف انتهاء الصلاحية) اتحل واتأكّد بدليل CI حقيقي كامل. Auto
re-login مؤجّل صراحة بطلب المستخدم — لو احتجناه لاحقًا هيبقى بند
منفصل.

### 16. Interstitials — إعلان كامل الشاشة بيمنع التقدم لحد ما يتقفل (محور 6، طلب المستخدم صراحة بعد Login/Session)

**الفكرة:** مسار جديد ومعزول تمامًا `/feed-interstitial` على mock-target
(`structural/interstitial.py` + `templates/feed_interstitial.html`) —
overlay حقيقي بيظهر **بعد** ما الصفحة تحمّل (مش بيمنع أول response زي
cookie wall)، ومهما ظهر بيمنع تحميل بيانات إضافية فعليًا (`loadMore()`
بترفض تجيب batch جديد وهو ظاهر) لحد ما يتقفل بزرار `[data-role="interstitial-close"]`
واضح. `/` و`/feed` الأصليين اتسابوا من غير أي تعديل — `/feed-interstitial`
بيستخدم content generator خاص بيه (`build_interstitial_feed_page`) ومسار
JSON منفصل تمامًا (`/api/feed-interstitial`) عشان ميشاركش state مع
`/feed`'s `FeedRateLimiter` أو DOM virtualization خالص.

**الفرق الجوهري عن كل التحديات التانية:** المحتوى الحقيقي **موجود في
الـ DOM طول الوقت** — الـ overlay عنصر شقيق (sibling) فوقه، مش بديل
عنه (زي ما المستخدم حدد صراحة). المشكلة مش "المحتوى مش موجود" (زي
Shadow DOM/DOM Virtualization)، لكن "فيه عنصر تفاعلي لازم يتم التعامل
معاه الأول قبل ما التقدم يكمل".

**قرار تصميمي حقيقي، مش عرضي:** الحجب بيتم عبر JS flag
(`window.__interstitialShown`) بيتفحص جوّه `loadMore()` نفسها، **مش**
`overflow: hidden` على الـ body. السبب: `src/providers/antibot/_scroll.py`
نفسه موثّق فيه إن `scroll_and_collect`/الحلقة الأساسية بتعمل
`dispatchEvent(new Event('scroll'))` **بغض النظر** عن إذا كان الموضع
الفعلي اتغيّر، فحجب CSS بس مكانش هيوقف الـ synthetic dispatch ده فعليًا
— اتقرر ده **قبل** ما أي كود يتكتب (verify don't assume تطبيق مباشر).

**Trigger قابل للتهيئة (`INTERSTITIAL_TRIGGER`): الاتنين متاحين فعليًا،
مش واحد بس:** `"time"` (بعد `INTERSTITIAL_DELAY_MS`) و`"scroll"` (بعد
نسبة `INTERSTITIAL_SCROLL_PERCENT`) — الاتنين مُنفّذين بالكامل ومُختبرين
بـunit tests (`render_interstitial_script`). الـ stack المشترك بتاع
live CI شغّال بوضع `"time"` بس (حتمي، وقت حقيقي منقضي مش threshold
عشوائي) — **قرار موثّق صراحة، مش نسيان:** وضع `"scroll"` مش هيتـlive-CI
-اختبر end-to-end الجولة دي، لأن الآلية الحالية لـ`click_selector`
(camoufox/patchright providers) بتضغط **قبل** أي محاولة scroll خالص
(مباشرة بعد `goto()`/login، قبل `post_load_wait_ms`) — فلو الـ
interstitial بيظهر بس بعد scroll فعلي، مفيش نقطة زمنية الـ click
الوحيد الحالي يقدر يوصلها فيها أصلًا. ده قيد معماري حقيقي في الآلية
الحالية، مش حل ناقص — لو احتجنا نحله (interstitial بيظهر أثناء الـ scroll
مش قبله) هيبقى بند منفصل لاحقًا، زي 2FA اتسجّل في بند 15.

**الاختبار بالترتيب المطلوب (بند 4 من طلب المستخدم):** الكود الحالي
(`GenericSpider` + الـ3 providers) اتجرّب **من غير أي تعديل** الأول ضد
`/feed-interstitial` عبر `mock_target_interstitial_camoufox_unhandled.yaml`
(من غير `click_selector`) — الفرضية الموثّقة *قبل* أي push: بما إن
`INTERSTITIAL_DELAY_MS=1000` أقل بكتير من `post_load_wait_ms` الافتراضي
لـ camoufox/patchright (5000ms)، الـ overlay هيظهر ويقفل التحميل قبل أي
محاولة scroll خالص، فبس أول batch (`INTERSTITIAL_FEED_PAGE_SIZE=5`
items، بيتحمّل تلقائيًا عند فتح الصفحة) هو اللي هيترجّع — مش صفر (الصفحة
مش عاطلة)، ومش الكل كمان (فيه عائق حقيقي). **الحل المتوقع (بند 5):**
بالظبط زي ما المستخدم توقّع — نفس آلية `click_selector` بتاعة cookie
wall بالحرف، من غير أي ميكانيزم جديد: `mock_target_interstitial_camoufox_dismissed.yaml`
و`mock_target_interstitial_patchright_dismissed.yaml` بيضيفوا
`click_selector: '[data-role="interstitial-close"]'` بس — Playwright's
actionability checks بتخلي الـ click ينتظر لحد ما الزرار يبقى visible
فعليًا (بما إن الـ overlay بيبدأ `display:none`)، فبيقفل الـ interstitial
قبل أي scroll مهما كانت مدة التأخير، وكل الـ batches الـ3
(`INTERSTITIAL_FEED_PAGE_SIZE * INTERSTITIAL_FEED_TOTAL_BATCHES = 15`
item) بترجع طبيعي.

**اتّحقّق محليًا** قبل الدفع: ruff نظيف (`src/`، `tests/`،
`test-environment/`)، `mypy --strict` نظيف (`src/` — مفيش تعديل على
interfaces خالص، `click_selector` كان موجود بالفعل)، نفس أوامر CI
بالظبط: `pytest tests/unit --cov=src --cov-fail-under=85` → 264 passed،
86.08% (مفيش تغيير — الجولة دي معماريًا كلها YAML configs جدد + مسار
mock-target جديد، من غير أي كود `src/` جديد)، `pytest tests/contract`
→ 29 passed، `test-environment`'s own suite → 158 passed، **100%
coverage** (36 statement جديدة في `structural/interstitial.py` مغطّاة
بالكامل). الاختبارات الحية الـ3 الجديدة
(`test_unhandled_interstitial_blocks_further_loading_after_the_first_batch`،
`test_camoufox_dismisses_the_interstitial_and_yields_every_batch`،
`test_patchright_dismisses_the_interstitial_and_yields_every_batch`)
اتّجمّعت (collected) واتخطّت (skipped) نظيف محليًا (من غير
`TITAN_BYPARR_URL`) — **لسه محتاجين تأكيد CI حقيقي** للنتيجة الفعلية
(هل الفرضية أعلاه صحيحة فعلًا؟) — هتتسجّل هنا بمجرد ما الـ run يخلص.

## Antibot Provider Comparison (نتايج حقيقية، مش افتراض)

مقارنة مبنية بالكامل على نتايج CI حقيقية من الجولات 1-4 (runs
32479883962 إلى 32524934383)، مش تخمين نظري:

| | **Byparr** (`byparr_provider.py`) | **Camoufox** (`camoufox_provider.py`) | **Patchright** (`patchright_provider.py`) |
|---|---|---|---|
| **آلية العمل** | HTTP delegation لخدمة خارجية (`/v1` API)، Chromium جوه الخدمة | متصفح Firefox-based حقيقي (Camoufox) بيتشغّل in-process | متصفح Chromium حقيقي (Patchright، drop-in Playwright replacement + stealth layer) بيتشغّل in-process |
| **تحكم بتوقيت إغلاق المتصفح** | ❌ لأ — بيقفل فور ما `load` event يحصل، مفيش parameter لانتظار إضافي (اتأكّد منه فعليًا، README اتفحص) | ✅ أيوه — `post_load_wait_ms` قابل للتهيئة (افتراضي 5 ثواني) | ✅ أيوه — نفس `post_load_wait_ms` بالظبط، بس (اتأكّد فعليًا) معندوش فرصة يأثّر لو Anubis رفض الطلب قبل `load` أصلاً |
| **تحديات Cloudflare Turnstile/WAF كلاسيكية** (`scrapingcourse.com/antibot-challenge`) | ✅ نجح فعليًا (`test_byparr_live_solve.py`، متكرر عبر كل الجولات) | لسه ملحقّقش عليه فعليًا لهذا النوع تحديدًا — مش مُختبر | لسه ملحقّقش عليه فعليًا لهذا النوع تحديدًا — مش مُختبر |
| **تحديات async post-load زي Anubis's PoW الحقيقي** | ❌ فشل فعليًا وبثبات (rounds 1-3، دائمًا 0 items لسبب معماري: بيقفل قبل ما الـ JS الـ async يشتغل) | ✅ نجح فعليًا (round 3، run 32507637737 — items > 0 حقيقية) | ❌ فشل فعليًا (round 4، run 32524934383) — **لسبب مختلف عن الاتنين**: Anubis's `bot/headless-chrome` fingerprint rule بيرفضه explicit deny قبل حتى مرحلة التحدي |
| **الاعتمادية الخارجية** | محتاج خدمة Byparr شغّالة (container/service منفصل) | مفيهوش — بيتشغّل بالكامل في نفس process بتاع Scrapy | مفيهوش — بيتشغّل بالكامل في نفس process بتاع Scrapy |
| **الاستهلاك** | أخف (HTTP call بس من طرفنا) | أتقل (متصفح Firefox حقيقي بيتشغّل محليًا لكل طلب) | أخف من Camoufox (بيعيد استخدام Playwright/Chromium الموجود + stealth layer بس)، بس أتقل من Byparr |

**الخلاصة الحقيقية (محدّثة بعد round 4):** مفيش "أداة أفضل مطلقًا" —
كل واحدة أقوى (أو أضعف) في نوع تحدي مختلف، بناءً على أدلة حقيقية مش
افتراض:
- **Byparr** لسه الافتراضي (`antibot_provider: byparr`) لأنه أخف
  وأثبت نفسه فعليًا ضد تحديات WAF/Cloudflare-style الكلاسيكية.
- **Camoufox** (`antibot_provider: camoufox`) هو الاختيار الصح لأي
  تحدي بيعمل شغل حقيقي *بعد* `load` event (زي Anubis's real PoW) —
  ده بالظبط نوع التحدي اللي Byparr's API الحالي مبنيًا هيكليًا إنه
  يفشل فيه (مفيش parameter انتظار إضافي في بروتوكوله).
- **Patchright** (`antibot_provider: patchright`) أخف من Camoufox
  فعليًا، بس **مش بديل عنه لتحديات fingerprint-based زي Anubis's
  `bot/headless-chrome` rule** — اتأكّد فعليًا إنه بيترفض قبل حتى
  مرحلة التحدي. الخيار الصح ليه (لسه لازم يتأكّد فعليًا، مش افتراض)
  هو تحديات أخف مايحتجوش تحمّل fingerprint كامل مختلف عن Chromium
  العادي — مش تحديات بتستهدف Chromium/headless-Chrome تحديدًا.

**فجوة Byparr نفسها (upstream) لسه مسجّلة ومفتوحة** — مقترح GitHub
issue كامل مكتوب في `docs/byparr-post-load-wait-issue-draft.md`
(مش مرفوع فعليًا — الـ session ده مالوش صلاحية GitHub API لمستودعات
تانية غير `malekazmy00`'s، اتأكّد منه فعليًا عبر `add_repo`).
