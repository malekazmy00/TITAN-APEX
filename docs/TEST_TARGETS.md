# قائمة مواقع الاختبار التدريبية (Test Targets Collection)

> كل المواقع هنا مُعدّة رسميًا للتدريب/الاختبار - قانونية 100% ومسموح بيها صراحة من أصحابها.
> الهدف: تغطية واسعة من السيناريوهات (ثابت → ديناميكي → anti-bot → متغيّر) تحاكي التنوع الحقيقي.

---

## المستوى 1 — أساسيات (HTML ثابت، بدون حماية)

| الموقع | التحدي |
|---|---|
| `quotes.toscrape.com` | pagination بسيط، نصوص |
| `books.toscrape.com` | كتالوج منتجات، فلترة، تسعير |
| `scrapethissite.com/pages/simple` | استخراج بيانات جدولية |
| `scrapethissite.com/pages/forms` | نماذج بحث + pagination |

## المستوى 2 — تعقيد بنيوي (مازال بدون حماية)

| الموقع | التحدي |
|---|---|
| `scrapethissite.com/pages/frames` | frames وiFrames |
| `scrapethissite.com/pages/ajax-javascript` | محتوى محمّل بـ AJAX (أفلام أوسكار) |
| `quotes.toscrape.com/js` | نفس البيانات لكن مُرندرة بالكامل عبر JS |
| `quotes.toscrape.com/scroll` | infinite scroll |
| `quotes.toscrape.com/login` | نماذج تسجيل دخول وإدارة جلسات (أي username/password شغال) |
| `quotes.toscrape.com/tableful` | HTML قديم الطراز (جداول متداخلة) |

## المستوى 3 — محتوى ديناميكي حقيقي (JS-heavy)

| الموقع | التحدي |
|---|---|
| `scrapingcourse.com/ecommerce` | كتالوج ديناميكي (مُختبر بالفعل ✅) |
| `scrapingcourse.com/infinite-scrolling` | تحميل تدريجي بالسحب (مُختبر بالفعل ✅) |
| `scrapingcourse.com/javascript-rendering` | محتوى JS خالص (مُختبر بالفعل ✅) |
| `scrapingcourse.com/pagination` | pagination رقمية (مُختبر بالفعل ✅) |
| `webscraper.io/test-sites/pagination` | pagination رقمية (مُختبر بالفعل ✅) |
| `webscraper.io/test-sites/scroll` | infinite scroll (`data-next-page` marker) |
| `webscraper.io/test-sites/load-more` | زرار "Load More" — click-triggered، مش scroll؛ نتيجة الاختبار الفعلي في تقرير Tier 2 |
| `webscraper.io/test-sites/website-state-setup-login` | تسجيل دخول — نفس قيد `quotes.toscrape.com/login` (Known Spider Limitation) |

> **ملحوظة (2026-08-21):** عند الفحص الفعلي، الكتالوج الحالي لـ
> `webscraper.io/test-sites` بقى 4 صفحات بس (مش "أكتر من 10 أنماط" زي
> ما كان متوقع) — `pagination`، `scroll`، `load-more`،
> `website-state-setup-login`. مفيش صفحات "dropdown filters" أو "nested
> categories" منفصلة موجودة على الموقع الحي دلوقتي.

## المستوى 4 — حماية Anti-bot متدرجة

| الموقع | التحدي |
|---|---|
| `scrapingcourse.com/antibot-challenge` | حماية متوسطة (مُختبر بالفعل ✅) |
| `scrapingcourse.com/cloudflare-challenge` | Cloudflare Turnstile حقيقي، حماية على مستوى WAF (مُختبر بالفعل ✅) |
| `nowsecure.nl` | Cloudflare Turnstile تاني، مستقل عن `scrapingcourse.com` — نتيجة الاختبار الفعلي في تقرير Tier 2؛ ملحوظة: التحدي هنا cosmetic/client-side بس (مش WAF block زي التاني) |

### أدوات تشخيص (مش targets بيانات — خطوة تحقق بعد أي محاولة تجاوز)

| الأداة | الاستخدام | الحالة |
|---|---|---|
| `bot.sannysoft.com` | فحص 20+ اختبار (webdriver, plugins, WebGL...) بيوضح هل الـ browser ظاهر كـ automated ولا لأ | مُدمج كـ diagnostic test (`tests/integration/test_sannysoft_diagnostic_live.py`) — بيسجّل النتيجة، مش بيفشّل CI على أساسها |
| `browserleaks.com` | فحص fingerprint شامل (canvas, WebGL, TLS) | متسجّل هنا بس لسه مش مُدمج — نفس فكرة sannysoft، هيتضاف لاحقًا لو احتجناه |
| `demo.fingerprint.com/web-scraping` | demo رسمي من Fingerprint.com لكشف الـ bots | متسجّل هنا بس لسه مش مُدمج |

> **`scrapingclub.com` اتشال من القائمة دي (2026-08-21):** عند الفحص
> الفعلي، الدومين بالكامل بقى بيرجّع موقع كازينو غير متعلق ("Winspirit
> Casino Australia") — مش الموقع التدريبي الأصلي خالص. اتأكد بـ fetch
> مباشر للصفحة الرئيسية (200، محتوى كازينو) + مسار تمرين قديم معروف من
> الموقع الأصلي (`/exercise/list_basic/` → 404). يعني الموقع اتغيّر
> ملكيته/محتواه من وقت ما القائمة دي اتعملت، ومش target تدريبي شرعي
> دلوقتي. التفاصيل الكاملة في `docs/REQUIREMENTS.md` قسم 6. لو محتاجين
> بديل لمستوى 4 لاحقًا، يتضاف هنا بعد نفس فحص `legal_status`.

## المستوى 5 — واقعي معقد (بيانات كبيرة الحجم، تغيّر مستمر)

| الموقع | التحدي |
|---|---|
| `oxylabs.io/sandbox` (Oxylabs Scraping Sandbox) | منصة e-commerce تجريبية كاملة، مصممة لمحاكاة متجر حقيقي |
| Wikipedia (أي صفحة عامة) | بيانات هيكلية حقيقية، حجم كبير، تغيّر مستمر - مسموح بالكامل حسب سياستهم |
| Hacker News (`news.ycombinator.com`) | API رسمي + HTML بسيط، بيانات تتغيّر لحظيًا (اختبار جيد للتعامل مع محتوى متغيّر) |

---

## مؤجّل — محتاج قرار قبل ما نبدأ

**SPA (Single-Page App) demo حقيقي** (زي `react-shopping-cart` أو مشروع
مفتوح المصدر مشابه): تحدي مختلف تمامًا عن "صفحة فيها JS" — كل التنقل
جوه JS من غير صفحات منفصلة أصلاً. مش هيتضاف هنا لحد ما يتحدد demo بعينه
(URL محدد) ويعدي فحص `legal_status` زي أي target تاني.

---

## استراتيجية الاستخدام المقترحة

1. **اختبار انحداري (regression) دوري:** المستويات 1-3 تتشغّل تلقائيًا في كل CI run (زي ما بيحصل بالفعل) - دول بيضمنوا إن أي تعديل جديد ملخبطش الأساسيات.
2. **اختبار توسّع أسبوعي:** أضف موقع جديد من المستوى 4-5 كل أسبوع لحد ما تغطي أكبر عدد ممكن - كل موقع جديد = config.yaml جديد بس (زي مبدأ التوسع اللي بنيناه).
3. **متابعة التغيّر:** Wikipedia وHacker News مفيدين تحديدًا لاختبار إزاي الكود بيتعامل لما البيانات تتغيّر بين تشغيلة وتانية (سيناريو واقعي جدًا).
4. **لا تستهدف أكتر من 1-2 requests/ثانية لأي موقع من دول** - حتى المواقع التدريبية بتقدّر الاستخدام المسؤول، وده بيدرّب الكود على rate limiting صح من البداية.

---

## ملحوظة قانونية

كل المواقع دي إما:
- مُصممة رسميًا لغرض التدريب (toscrape.com, scrapingcourse.com, scrapethissite.com, webscraper.io, Oxylabs Sandbox) - إذن صريح
- منصات عامة بسياسة استخدام واضحة تسمح بالوصول الآلي المعتدل (Wikipedia, Hacker News)
- demo pages تشخيصية مُعدّة عمدًا للاختبار الآلي/أدوات الـ scraping (`nowsecure.nl`, `bot.sannysoft.com`) - نفس مبدأ `scrapingcourse.com`'s anti-bot demos

لسه المبدأ نفسه ساري: أي target حقيقي (غير الموجودين هنا) لازم يعدي مراجعة `legal_status` مع المكتب القانوني قبل التفعيل، زي ما اتفقنا قبل كده.
