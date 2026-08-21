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
| `scrapingcourse.com/javascript-rendering` | محتوى JS خالص |
| `scrapingcourse.com/pagination` | أنماط pagination متعددة (رقمية، load-more، scroll) |
| `webscraper.io/test-sites` | مجموعة كاملة من متاجر تجريبية بأنماط مختلفة (categories, AJAX pagination) |

## المستوى 4 — حماية Anti-bot متدرجة

| الموقع | التحدي |
|---|---|
| `scrapingcourse.com/antibot-challenge` | حماية متوسطة (مُختبر بالفعل ✅) |
| `scrapingcourse.com/cloudflare-challenge` | Cloudflare Turnstile حقيقي (مُختبر بالفعل ✅) |

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

لسه المبدأ نفسه ساري: أي target حقيقي (غير الموجودين هنا) لازم يعدي مراجعة `legal_status` مع المكتب القانوني قبل التفعيل، زي ما اتفقنا قبل كده.
