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
| `scrapethissite_ajax_javascript.yaml` | 2 | المحتوى بيتحمّل بـ AJAX بس لما `document.location.hash` يبقى متظبط عند التحميل (أو click) — الحل: start URL فيه `#2015`. **احتمال فجوة زمنية حقيقية**: الموقع بيضيف تأخير JS متعمد (~1.5 ثانية) بعد رد الـ AJAX قبل ما يعرض الصفوف، وده أطول من انتظار الـ scroll-loop بعد الـ navigation — النتيجة الحقيقية موثّقة في تقرير Test Targets النهائي (اتفحصت فعليًا في CI، مش mفترضة) |
| `quotes_toscrape_js.yaml` | 2 | نفس بيانات quotes.toscrape.com لكن مُرندرة كاملة بـ JS من array مضمّن؛ نفس الـ selectors ونفس نمط pagination (`li.next a`) بالظبط، `render_js: true` بس اللي اتغير |
| `quotes_toscrape_scroll.yaml` | 2 | infinite scroll حقيقي (`div.quotes` فاضية في الـ HTML الثابت)، نفس نمط `scrapingcourse_infinite_scrolling.yaml` |
| `scrapingcourse_javascript_rendering.yaml` | 3 | الـ HTML الثابت فيه 13 placeholder فاضي (name/price موجودين بس من غير نص) — `render_js: true` إلزامي |
| `scrapingcourse_pagination.yaml` | 3 | ثابت بالكامل، 13 صفحة، `a.next-page` / `rel="next"` واضح |
| `webscraper_io_pagination.yaml` | 3 | اخترنا صفحة `pagination` (كتالوج سيارات) كتمثيل ملموس من كتالوج `webscraper.io/test-sites` المذكور بشكل عام في القائمة؛ حقل `details` (سنة/بلد/مسافة) بيرجع list واحد مش 3 حقول منفصلة لأن الثلاثة بياخدوا نفس الـ class بالظبط بدون أي تمييز — قيد في المعمارية الحالية (كل field له selector واحد جوه الـ item)، مش باج |

### فجوات مُكتشفة — مش هتتغطى بـ "صفر كود جديد" (موثّقة بصراحة)

| الموقع | المشكلة | السبب |
|---|---|---|
| `quotes.toscrape.com/login` | محتاج فعليًا FormRequest + استخراج CSRF token + إدارة session (POST) | `GenericSpider` بيعمل GET بس على `start_urls`، مفيهوش أي مسار لـ POST/forms — إضافته كود جديد حقيقي في `generic_spider.py`، مش config |
| `quotes.toscrape.com/tableful` | كل quote منتشر على صفين `<tr>` منفصلين (نص+مؤلف في سطر، الـ tags في سطر تاني)، والنص والمؤلف نفسهم ملزوقين في text node واحد من غير أي فاصل/class | معمارية `SelectorsConfig` الحالية بتفترض item واحد = عنصر HTML واحد بكل حقوله جواه؛ الحالة دي محتاجة تجميع صفوف متعددة و/أو تقطيع نص (regex) — كود جديد |

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
