# Test Environment Escalation Changelog

سجل رسمي لكل جولة تصعيد في صعوبة بيئة الاختبار — راجع
`docs/REQUIREMENTS.md` قسم 8 ("Escalation Cycle") للدورة الكاملة
والمبدأ اللي بيحكم القراءة الصحيحة لأي سطر هنا.

> **مبدأ أساسي (منقول حرفيًا من docs/REQUIREMENTS.md قسم 8):** "كل
> الاختبارات نجحت" في أي جولة تعني الكود قوي بما يكفي للمستوى الحالي
> من الصعوبة بس - مش دليل إن الكود جاهز للواقع، ومش وصلنا للحد
> الأقصى. السقف الحقيقي يفضل مجهول طالما بيئة الاختبار لسه بتتطور.

هذا الملف يبدأ فارغًا اعتبارًا من 2026-08-21 — أول جولة تصعيد رسمية
بعد إنشاء الدورة تُسجَّل هنا. تاريخ توسعة الـ Test Targets قبل كده
(المستويات 1-5، Tier 2، إصلاحات `render_wait_ms`/`click_selector`)
موثّق بالتفصيل في `docs/REQUIREMENTS.md` أقسام 6 و7 — مش معاد تسجيله
هنا بنفس الصيغة رجعيًا، عشان الترقيم بالشكل `X → X+1` يبدأ من نقطة
واضحة بدل ما يتفرض على تاريخ مش متسجل أصلاً بهذا الشكل.

## الصيغة الثابتة لكل سطر

```
[التاريخ] من مستوى X لمستوى X+1 - أضفنا كذا - نتيجة الكود (نجح كامل/فشل جزئي/فشل كامل) - القرار (نصلح دلوقتي/نأجل ونسجل/نقبله كحد)
```

- **نتيجة الكود**: واحدة من الثلاثة بالظبط — نجح كامل / فشل جزئي / فشل كامل
- **القرار**: واحد من الثلاثة بالظبط — نصلح دلوقتي / نأجل ونسجل / نقبله كحد
- تفاصيل أي فجوة مكتشفة (السبب الجذري، الحل المحتمل، الكود اللي
  اتضاف) تتوثّق كاملة في `docs/REQUIREMENTS.md` (قسم 6 لتغطية Test
  Targets، قسم 7 لـ Known Spider Limitations) — السطر هنا إشارة/فهرس
  بس، مش تكرار.

## السجل

<!-- كل جولة تصعيد جديدة تتضاف كسطر جديد فوق هنا، الأحدث أولاً. -->

[2026-08-21] من مستوى 4 لمستوى 5 (تصعيد **مركّب: بيئة + أداة مع بعض** — أول بند من `docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md`: cookie consent wall) - أضفنا (1) `click_selector` اختياري في عقد `AntibotProvider.solve()` نفسه (best-effort - Camoufox/Patchright بيعرفوا click حقيقي، Byparr بيسجّل تحذير واضح ومكمّل من غيره لأن `/v1` API بتاعه مفيهوش قدرة تفاعل خالص) + `ByparrMiddleware` بيمرّر `request.meta["click_selector"]` تلقائيًا و(2) cookie-consent wall حقيقي في `test-environment/mock-target` (`structural/cookie_wall.py` - محتوى غايب فعليًا من الاستجابة، مش overlay بـ CSS، لحد ما consent cookie تتحط عبر رابط Accept حقيقي) + الـ 3 `mock_target*.yaml` اتحدّثوا بـ `click_selector: "#accept-cookies"` - نتيجة الكود: **نجح كامل** لـ Camoufox (بيعدّي الاتنين مع بعض: Anubis ثم cookie wall، بـ click حقيقي على `#accept-cookies`) - Byparr وPatchright فضلوا صفر items **زي المتوقع بالظبط** لأنهم بيفشلوا أصلاً في مرحلة Anubis الأسبق قبل ما يوصلوا لـ cookie wall خالص (مش معلومة جديدة، موثّق صراحة إنه غير قابل للتمييز عن فجوتهم الأسبق) - مؤكد من CI run 32528886186، 23/23 اختبار PASSED - القرار: نصلح دلوقتي (تفاصيل كاملة في `docs/REQUIREMENTS.md` قسم 9 بند 8)

[2026-08-21] من مستوى 3 لمستوى 4 (تصعيد **في الأداة، مش في البيئة** — إضافة `PatchrightProvider` كخيار تالت أخف لـ `AntibotProvider`) - أضفنا `PatchrightProvider` implementation تالت (نفس معمارية `CamoufoxProvider`، بس Chromium + Patchright's stealth layer فوق Playwright الموجود بدل Firefox) + `antibot_provider: "patchright"` في `SpiderConfig` + contract/unit tests + `mock_target_patchright.yaml` وتشغيله فعليًا ضد نفس تحدي Anubis - نتيجة الكود: **فشل جزئي بمعلومة جديدة قيّمة** (0 items، بس السبب مختلف تمامًا عن فجوة Byparr: Anubis's `bot/headless-chrome` fingerprint rule بيرفض Patchright صراحة (`"msg":"explicit deny"`, `rule:"DENY"`) قبل حتى مرحلة تحدي الـ proof-of-work — يعني `post_load_wait_ms` (سبب وجود Patchright) معندوش فرصة يأثّر خالص هنا، بعكس فجوة Byparr التوقيتية. مؤكد من CI run 32524934383 (21/22 اختبار PASSED، الفاشل الوحيد هو اختبار Patchright نفسه لأنه وقتها كان لسه بيفترض النجاح؛ الاختبار اتحوّل بعدها لـ regression sentinel يوثّق النتيجة الحقيقية (صفر items) زي نمط Byparr بالظبط - **تأكيد نهائي فعلي في CI run 32527047011، 22/22 اختبار PASSED**)) - القرار: نقبله كحد (فجوة fingerprint-based، مش قابلة للحل بمجرد إضافة انتظار زي فجوة Byparr التوقيتية؛ Camoufox already نجح فعليًا لنفس نوع التحدي ده، فمفيش داعي لمزود رابع (nodriver) بناءً على شرط المستخدم الصريح: "لو الاتنين فشلوا" - الاتنين مفشلوش، Camoufox نجح). تفاصيل كاملة + جدول مقارنة محدّث للـ 3 providers في `docs/REQUIREMENTS.md` قسم 9 بند 7 و"Antibot Provider Comparison"

[2026-08-21] من مستوى 2 لمستوى 3 (تصعيد **في الأداة، مش في البيئة** — أول مرة نضيف AntibotProvider تاني بدل ما نزوّد صعوبة mock-target نفسه) - أضفنا `CamoufoxProvider` implementation تاني لـ `AntibotProvider` (يشغّل Camoufox حقيقي in-process، بيتحكم بنفسه في توقيت إغلاق المتصفح عبر `post_load_wait_ms` قابل للتهيئة) + حقل `antibot_provider: "byparr" | "camoufox"` اختياري في `SpiderConfig` (افتراضي byparr) + contract tests متوازية للاتنين + `mock_target_camoufox.yaml` وتشغيله فعليًا ضد نفس تحدي Anubis اللي Byparr فشل فيه - نتيجة الكود: نجح كامل (بعد 3 إصلاحات حقيقية متتالية في نفس الجولة: (1) crash فوري بسبب استدعاء Playwright's sync API على reactor thread بتاع Scrapy's asyncio - اتصلح بـ deferToThread زي PlaywrightMiddleware بالظبط؛ (2) `COOKIE_PARTITIONED=false` كان اتّحقّق منه يدويًا في round 2 بس ما اتضافش فعليًا لـ docker-compose.test.yml - غلطة حقيقية اتصلحت؛ بعد الاتنين، Camoufox عدّى تحدي Anubis الحقيقي بالكامل ورجّع posts حقيقية - CI run 32507637737، 21/21 اختبار PASSED) - القرار: نصلح دلوقتي (تفاصيل كاملة + "Antibot Provider Comparison" الحقيقية بين Byparr وCamoufox في `docs/REQUIREMENTS.md` قسم 9؛ فجوة Byparr نفسه upstream لسه مسجّلة ومفتوحة، مقترح issue مكتوب بس مش مرفوع بسبب قيد صلاحيات الـ session)

[2026-08-21] من مستوى 1 لمستوى 2 - أضفنا (1) `byparr` instance مخصص جوه `docker-compose.test.yml` بـ `network_mode: "service:anubis"` (يشارك network namespace بتاع Anubis حرفيًا، بدل ما يفضل `services:` container منفصل على شبكة تانية زي الجولة اللي فاتت) و(2) `COOKIE_SECURE=false` + `COOKIE_PARTITIONED=false` على Anubis (flags حقيقية موثّقة في كوده المصدري، مش TLS — Anubis مفهوش HTTPS server مدمج أصلاً) - نتيجة الكود: فشل جزئي (0 items لسه - بس دلوقتي Byparr فعليًا بيوصل لـ Anubis ويستلم تحدي proof-of-work حقيقي كل مرة (اتّحقّق منه محليًا: لوج Anubis `"new challenge issued"`, weight=10)، مش زي الجولة اللي فاتت اللي مكانش بيوصل أصلاً؛ فجوة تالتة جديدة اكتشفت: Byparr's API بيقفل الـ browser context فور ما `load` event يحصل، قبل ما JS الـ challenge الـ async يشتغل خالص - اتّحقّق منه محليًا بمراقبة لوج Anubis لأكتر من 20 ثانية من غير أي نشاط تاني؛ الاختبارين الحيين اتأكّدوا PASSED فعليًا في CI run 32500347313، والـ stack (mock-target + anubis + byparr المخصص) اتبنى واتشغّل بنجاح فيه (byparr healthy خلال ~14 ثانية)) - القرار: نأجل ونسجل (تفاصيل كاملة للفجوة الجديدة في `docs/REQUIREMENTS.md` قسم 9 بند 4 - تحتاج كود جديد في `ByparrProvider` أو تعديل upstream في Byparr نفسه، مش تعديل سريع)

[2026-08-21] من مستوى 0 لمستوى 1 - أضفنا mock-target خلف Anubis (PoW الحقيقي، policy افتراضية أصلية) + BotD (log-only) + 4 تحديات هيكلية (markup randomizer، honeypots، decoy data، /feed) + `src/spiders/configs/mock_target.yaml` وتشغيله فعليًا ضد الـ stack الكامل في CI (run 32479883962) - نتيجة الكود: فشل جزئي (0 items - في CI الحقيقي، Byparr's services: container مقدرش يوصل لـ Anubis خالص بسبب اختلاف شبكة Docker، فالطلب رجع لـ plain Scrapy اترفض صراحة من Anubis؛ فجوة تانية مستقلة اتأكدت محليًا: حتى لو Byparr وصل فعلاً، تحدي Anubis مقدرش يكتمل فوق HTTP عادي بسبب Secure cookie؛ الكراول خلص من غير crash، مفيش honeypot اتلمس) - القرار: نأجل ونسجل (تفاصيل كاملة للفجوتين + إصلاح جانبي حقيقي اتنفذ فورًا لـ `TITAN_BYPARR_URL` env fallback في `docs/REQUIREMENTS.md` قسم 9)
