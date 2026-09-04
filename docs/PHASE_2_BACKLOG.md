# Phase 2 Backlog — فحص تقرير خارجي غير مؤكد (جرد وتخطيط، صفر تنفيذ)

> **مصدر هذا المستند**: تقرير خارجي (مصدر غير مؤكد، مش من فريقنا) بيدّعي
> وجود فجوات تقنية معيّنة. كل ادعاء فيه اتفحص هنا **بدليل حقيقي من الكود
> نفسه** (قراءة مباشرة لملفاتنا + مصدر مكتبات طرف ثالث المُثبَّتة فعليًا،
> مش افتراض ولا ثقة عمياء في التقرير). القرار لكل بند: قبول أو رفض بناءً
> على الواقع.
>
> **قاعدة صارمة، مش تفضيل**: أي فجوة اتأكدت هنا موثّقة **كتحسين لبيئة
> الاختبار الذاتية (`test-environment/`) بتاعتنا فقط** — صفر ذكر لأي
> موقع إنتاج حقيقي بالاسم في أي مكان (كود، تعليق، توثيق).
>
> **هذا المستند تخطيط بس — صفر تنفيذ فعلي.** التنفيذ يستنى مراجعة
> بشرية بعد قراءة هذا الجرد.

---

## الملخص السريع

| # | الادعاء | القرار |
|---|---|---|
| 1 | Mouse Movement (velocity/acceleration/pause/overshoot/context) | **فجوة حقيقية مؤكدة** |
| 2 | Keystroke Dynamics (بند 21 Login) | **فجوة حقيقية مؤكدة** |
| 3 | Canvas/WebGL Fingerprint | **موجود جزئيًا — تصحيح لإطار التقرير نفسه** (مش "partial بلا rotation" زي ما قال، لكن حقيقة مختلفة تمامًا) |
| 4 | AudioContext Fingerprint | **موجود بالفعل لـCamoufox** / **فجوة حقيقية لـPatchright** |
| 5 | WebRTC Leak Prevention | **فجوة حقيقية مؤكدة (الاتنين providers)** |
| 6 | HTTP/2 Frame Behavior | **غير منطقي لسياقنا، مؤكد بدليل** |
| 7 | Session Behavioral Modeling | **فجوة حقيقية مؤكدة، مختلفة عن rate limiter** |

---

## 1. Mouse Movement — فجوة حقيقية مؤكدة

**الادعاء**: Oxymouse Bezier curves موجودة، لكن ناقصنا velocity variation، acceleration curves، pause mid-path، overshoot-and-correct، context-aware movement.

**الدليل الحقيقي** (`src/providers/antibot/_mouse_movement.py`، مستخدم في `camoufox_provider.py`/`patchright_provider.py`):

- Oxymouse **موجود ومستخدم فعليًا**، بس في **نقطة استدعاء واحدة بس**:
  `_hover_feed_container_before_scroll` — تحريك الماوس فوق الـfeed
  container **قبل كل محاولة scroll** (`camoufox_provider.py:1004`،
  `patchright_provider.py:516`). مفيش استخدام تاني لأي مكان — كل
  التفاعلات التانية (`click_selector` لقبول الكوكيز، زرار submit
  الـlogin، زرار "Load More") بتستخدم `page.click()`/
  `page.locator(...).click()` العادي المباشر (`camoufox_provider.py:704,
  1019`) — **صفر مسار منحني لأي click**، بس للـhover قبل السكرول.
- `move_mouse_along_path` (نفس الملف): الحلقة
  `for x, y in path: page.mouse.move(x, y)` **بدون أي `wait_for_timeout`
  أو delay بين النقط خالص** — يعني كل نقط المسار بتتبعت بأسرع ما
  Playwright/CDP يقدر، بدون أي فرق توقيت. **تأكيد مباشر**: صفر velocity
  variation، صفر acceleration curve (محتاجين توقيت متغيّر بين النقط
  عشان يظهروا أصلًا).
- **صفر pause mid-path**: نفس السبب، الحلقة معندهاش أي `sleep`/`wait`.
- **Overshoot-and-correct مش موجود — وده قرار متعمد بالعكس**: التعليق في
  نفس الملف بيقول صراحة إن خوارزميتي `"gaussian"`/`"perlin"` (اللي
  بيعملوا overshoot حقيقي) **اتفحصوا ورُفضوا** لأن الـovershoot بتاعهم
  "a real defect, not a stealth feature" — و`move_mouse_along_path`
  دايمًا بيضيف حركة أخيرة **بالظبط** على الهدف بغض النظر عن مسار
  الخوارزمية، عشان يمحي أي overshoot أصلًا.
- **Context-aware movement مش موجود**: `move_mouse_along_path` دالة عامة
  واحدة، مفيش تفريق بين click/hover/drag — والاستدعاء الوحيد أصلًا
  hover بس.

**الخلاصة**: التقرير صح 100%. الفجوة حقيقية ومحددة بدقة.

### التصميم المقترح (test-environment فقط)

- endpoint جديد في mock-target: `GET /mouse-behavior-check` — صفحة
  فيها منطقة تفاعل (زي feed container موجود) بتسجّل كل `mousemove`
  event (timestamp + x + y) عبر JS عادي (مش vendored، كودنا احنا، نفس
  فلسفة `fpscanner_integration.py`).
- endpoint `POST /mouse-behavior-report` (نفس شكل `/botd-report`
  بالظبط): بياخد سلسلة الأحداث ويحسب:
  - **معامل تباين السرعة** بين النقط المتتالية (real human: تباين
    ملحوظ؛ حركتنا الحالية: سرعة شبه ثابتة بسبب غياب delay).
  - **وجود/غياب pauses** (فجوات زمنية > threshold بين نقط متتالية).
  - **معامل الـsmoothness** (تغيّر الاتجاه المفاجئ = بوت، انحناء
    تدريجي = إنسان).
  - نقاط **log-only** (زي `fpscanner_integration.py` بالظبط — INFO
    دايمًا، صفر إنفاذ) — مش حكم `bool` فردي.
- `config.py`: `ENABLE_MOUSE_BEHAVIOR_CHECK` (افتراضي `true`) +
  `MOUSE_BEHAVIOR_LOG_PATH`، نفس نمط `fingerprint_log_path`.

---

## 2. Keystroke Dynamics — فجوة حقيقية مؤكدة

**الادعاء**: بند 21 (Login) بيستخدم `page.fill()` بدل محاكاة توقيت كتابة بشري.

**الدليل الحقيقي** (`src/providers/antibot/_login.py`، `submit_login_form`):

```python
page.fill(username_field, username)
page.fill(password_field, password)
```

`page.fill()` بيحط القيمة في الحقل عبر عملية DOM واحدة (بيطلق
input/change events، لكن **بدون أي محاكاة كتابة حرف-بحرف ولا توقيت
متغيّر بينهم**) — الدالة دي مستخدمة في مكانين بس (`submit_login_form`
نفسها) وده كل مسار الـlogin الوحيد في المشروع. صفر استخدام لـ
`page.type()` أو أي محاكاة توقيت مخصصة في أي مكان.

**الخلاصة**: التقرير صح 100%.

### التصميم المقترح (test-environment فقط)

- الصفحة الموجودة بالفعل `/login` (`security/auth.py`) تتوسّع بـJS
  بسيط يسجّل توقيت `keydown` لكل حرف في حقل username/password.
- endpoint `POST /keystroke-report`: يحسب inter-keystroke interval
  variance — كتابة `page.fill()` هتظهر كـ**event واحد فوري** (صفر
  intervals متعددة أصلًا) بدل سلسلة توقيتات — إشارة حتمية وواضحة،
  حتى بدون إحصاء معقد.
- نفس مبدأ log-only + INFO دايمًا.

---

## 3. Canvas/WebGL Fingerprint — تصحيح لإطار التقرير، مش قبول حرفي

**الادعاء**: Camoufox بيغطي جزء بس من غير rotation/consistency checking.

**الدليل الحقيقي** (قراءة مباشرة لمصدر حزمة `camoufox` v0.5.5 المُثبَّتة،
`camoufox/utils.py`):

```python
# Set random seeds for fingerprint noise (per launch)
set_into(config, 'fonts:spacing_seed', randint(1, 4_294_967_295))
set_into(config, 'audio:seed', randint(1, 4_294_967_295))
set_into(config, 'canvas:seed', randint(1, 4_294_967_295))
```

الأسطر دي **بتتنفذ دايمًا، بدون شرط**، في كل `launch_options()` call —
يعني كل مرة `CamoufoxProvider.solve()` بيفتح `Camoufox(headless=True)`
جديد (كل solve() بيفتح process منفصل تمامًا)، الـcanvas seed **بيتدوّر
تلقائيًا لوحده** — رغم إن كودنا احنا مش بيمرّر أي config صريح لكده.
يعني **"partial بدون rotation" اللي التقرير بيقوله مش دقيق** — الـ
rotation موجود فعليًا وتلقائي.

**لكن** — ده مش معناه صفر فجوة. من فحص فعلي سابق موثّق في
`docs/REQUIREMENTS.md` قسم 9 بند 19 (مش تخمين جديد، فحص حقيقي اتعمل
قبل كده وأُكِّد مرتين متتاليتين عبر CI حقيقي):

> `canvas.getContext('webgl')` رجع `null` فعليًا على Camoufox عندنا —
> **غياب تام**، مش قيمة مزيّفة.

يعني WebGL بالذات **مش "جزئي بلا rotation"** زي ما التقرير بيقول —
هو **غائب تمامًا** في بيئتنا (مفيش GPU حقيقي في بيئة headless
sandboxed، فمفيش context حقيقي أصلًا يقدر Camoufox يكذب من خلاله على
قيمه). ده **فجوة مختلفة الطبيعة تمامًا** عن اللي التقرير وصفها — وهي
**مُكتشفة وموثّقة ومُراقَبة بالفعل** عبر `fpscanner_integration.py`
(log-only score، مؤكد فعليًا في `test-environment/logs/fingerprint_reports.log`
بمثال حقيقي `{"webglAvailable": false, ...}` مكرر مرتين).

**ملحوظة أمانة**: الفحص الأصلي (بند 19) واختباري لملف الحزمة الحالية
تمّا في جلستين مختلفتين — ماحاولتش أعيد فتح متصفح Camoufox حي في
الجلسة دي (يحتاج launch حقيقي)، فأنا بعتمد على الدليل المسجّل سابقًا +
قراءة الكود الحالي، مش تكرار تجريبي حي الآن.

**Patchright مختلف تمامًا**: قراءة مباشرة لـ`patchright_provider.py` —
بيستخدم `browser.new_context(user_agent=...)` **العادي** (لا شيء تاني)،
صفر أي canvas/webgl/audio config. Patchright من الأساس مصمم لحاجة
مختلفة (توثيقه الخاص: "patches Chromium's own automation fingerprints
out at the driver level" — يعني إزالة إشارات الأتمتة/CDP، مش تدوير
بصمة fingerprint زي Camoufox/BrowserForge).

**الخلاصة**: مش قبول حرفي للتقرير ولا رفض كامل — **تصحيح دقيق**:
Camoufox عنده canvas+audio rotation حقيقي تلقائي (التقرير غلط هنا)،
WebGL غائب تمامًا (فجوة مختلفة، موثقة ومراقبة بالفعل مش جديدة)،
Patchright صفر تغطية فعلًا (التقرير صح هنا، لكن لسبب مختلف: مش لأنه
"جزئي" — لأن Patchright أصلًا مش مصمم لكده).

### التصميم المقترح (test-environment فقط)

- توسعة `fpscanner_integration.py` الموجودة بالفعل (مش ملف جديد): إضافة
  فحص **canvas consistency** — استدعاء `toDataURL()` مرتين في نفس
  الصفحة والتأكد إن القيمتين **متطابقتين** (noise حقيقي بيكون ثابت لكل
  session، مش عشوائي لكل استدعاء — عشوائية-فرط-الحد نفسها إشارة).
- تسجيل صريح فرق Camoufox vs Patchright في نفس التقرير (`provider` field
  موجود بالفعل في نمط تسجيل هذا المشروع) — عشان نفرّق البيانات
  التاريخية حسب provider.

---

## 4. AudioContext Fingerprint — نتيجتان مختلفتان حسب الـprovider

**الادعاء**: غير موجود تمامًا.

**الدليل الحقيقي**: نفس السطر المذكور في بند 3 فوق —
`set_into(config, 'audio:seed', randint(...))` بيتنفذ **دايمًا** في كل
launch لـCamoufox، بدون شرط. توثيق الحزمة نفسها (`camoufox/async_api.py`
docstring): *"Each context gets its own real fingerprint preset ...
with unique seeds for audio, canvas, and font spacing noise."*

**الخلاصة**:
- **Camoufox**: التقرير **غلط** — AudioContext noise موجود بالفعل،
  تلقائي، من غير أي config إضافي من عندنا.
- **Patchright**: التقرير **صح** — صفر تغطية (نفس السبب المذكور في
  بند 3: `browser.new_context()` عادي، صفر config).

### التصميم المقترح (لو قررنا نسدّ فجوة Patchright)

مفيش حل بسيط زي Camoufox (Patchright مبني على Chromium العادي، مفيش
له نظام fingerprint injection مدمج). لو الأولوية عالية مستقبلًا: حقن
`init_script` مخصص (`context.add_init_script(...)`) بيعمل override
بسيط لـ`AudioContext.prototype`/`AnalyserNode` بضجيج ثابت لكل جلسة —
ده تنفيذ حقيقي جديد مش استخدام مكتبة جاهزة، فمحتاج قرار منفصل صراحة
(خارج نطاق هذا الجرد).

---

## 5. WebRTC Leak Prevention — فجوة حقيقية مؤكدة (الاتنين providers)

**الادعاء**: غير موجود — ممكن الـIP الحقيقي يتسرب حتى لو باقي الطبقات شغالة.

**الدليل الحقيقي**:

- **Camoufox عنده القدرة دي جاهزة في مكتبته، إحنا مش بنستخدمها**: قراءة
  مباشرة لـ`camoufox/utils.py`'s `launch_options()`:
  `block_webrtc: Optional[bool] = None` — باراميتر حقيقي جاهز
  ("Whether to block WebRTC entirely"). كودنا (`camoufox_provider.py:493`)
  بيستدعي `Camoufox(headless=True)` **بس** — صفر `block_webrtc`، صفر
  `webrtc_ip`، صفر `geoip`/`proxy`. نتيجة مباشرة من قراءة الكود:
  `if not block_webrtc:` (شرط سلبي، `None` = falsy) بيدخل مسار
  الـspoofing **بس فقط لو `geoip` مضبوطة** — وإحنا مش ضابطينها، يعني
  صفر spoofing config يتطبق، وWebRTC **يفضل شغّال بإعدادات Firefox
  الافتراضية** (`media.peerconnection.enabled` يفضل `True` لأننا مش
  عدّلناه).
- **Patchright صفر قدرة من الأساس**: Chromium الحقيقي بتاعه بدون أي
  تعديل على WebRTC stack — مفيش حتى مكتبة نقدر نفعّلها، محتاجين حل
  مستقل (زي `--force-webrtc-ip-handling-policy` كـChromium launch flag).

**الخلاصة**: التقرير صح 100%، وده **أسهل فجوة نسدّها** من الكل —
Camoufox عنده الحل جاهز، بس محتاج سطر واحد (`block_webrtc=True` أو
`webrtc_ip=`) في استدعاء موجود بالفعل.

### التصميم المقترح (test-environment فقط)

- endpoint جديد `GET /webrtc-leak-check`: صفحة بتعمل
  `RTCPeerConnection` حقيقي مع STUN server محلي (أو حتى بدون أي STUN
  خارجي — مجرد `createDataChannel` + `createOffer` كفاية لتوليد ICE
  candidates محلية) وتقرأ IP addresses من الـ`onicecandidate` events
  عبر JS.
- `POST /webrtc-leak-report`: يسجّل كل IP اتسرب (لو فيه) — لو ظهر أي
  IP غير الـloopback/الوهمي المتوقع، ده تسريب حقيقي.
- نفس فلسفة log-only، وده بالذات نوع فحص **حتمي** (مش إحصائي زي
  الماوس/الكتابة) — إما فيه تسريب أو لأ.

### ✅ منفَّذ — `docs/REQUIREMENTS.md` section 9 entry 33، Item 1

التصميم فوق اتنفّذ بالحرف تقريبًا (فرق واحد بس: تصنيف mDNS بالاسم
مضاف صراحة كفئة "آمن" ثالثة، مش مجرد "مش loopback فمتوقّع" — نفس
الروح). `block_webrtc` باراميتر حقيقي جديد على
`AntibotProvider.solve()` (كامل لـCamoufoxProvider، تحذير log-only
لـPatchright/Byparr). `security/webrtc_leak_detector.py` +
`/webrtc-leak-check`/`/webrtc-leak-report` كلهم موجودين وشغالين
(test-environment: 296/296، coverage 99%). التفاصيل الكاملة (الأنبوب،
البق اللي اتلقط، الاختبارات، دليل الـCI) في `docs/REQUIREMENTS.md`
entry 33's own section الخاص بالبند ده.

---

## 6. HTTP/2 Frame Behavior — غير منطقي لسياقنا، مؤكد بدليل

**الادعاء**: نمط frames على مستوى HTTP/2 ممكن يبقى إشارة.

**الدليل الحقيقي**:

- `python3 -c "import h2"` → **`ModuleNotFoundError`** — حزمة `h2`
  (المطلوبة لـScrapy's `H2DownloadHandler`) **مش مثبتة في المشروع
  أصلًا**، ومفيش `DOWNLOAD_HANDLERS` مُعرَّفة في أي مكان بالكود
  (`grep` شامل صفر نتيجة). يعني كل طلبات Scrapy العادية (غير antibot)
  بتتكلم **HTTP/1.1 بس** — مفيش HTTP/2 يتفاوض عليه أصلًا، فمفيش frames
  نتكلم عنها من الأساس في المسار ده.
- كل المسارات المحمية/JS-rendered بتعدي عبر **متصفحات حقيقية كاملة**
  (Camoufox = Firefox حقيقي، Patchright/Playwright/Byparr = Chromium
  حقيقي) — الـstack الشبكي بتاعهم HTTP/2 **أصلي 100%**، مش محاكى، ومش
  حاجة إحنا بنبنيها أو نقدر "نزوّرها" — المتصفح نفسه بيتكلم بروتوكوله
  الحقيقي.

**الخلاصة**: النوع ده من البصمة بيبقى مشكلة لمشروع بيستخدم **HTTP
client خام** (زي `curl`/`requests` بيحاول يتظاهر إنه متصفح) — إحنا مش
كده، إحنا بنسوق متصفحات حقيقية. **التقرير نفسه اقترح احتمال إنه غير
منطقي لسياقنا، والدليل بيأكد كده بالظبط.** صفر تصميم مقترح — مفيش حاجة
نبنيها هنا.

---

## 7. Session Behavioral Modeling — فجوة حقيقية مؤكدة (مستوى مختلف عن الموجود)

> **ملحوظة تسمية**: التقرير سمّاها "ML evasion" — الاسم ده اتغيّر هنا
> عمدًا. إحنا مش بنعمل evasion لموقع بعينه، الهدف محاكاة سلوك جلسة
> واقعي عمومًا كطبقة دفاعية عامة.

**الادعاء**: محاكاة جلسة تصفح كاملة (وقت على الصفحة، scroll pattern،
clicks، idle time، tab switches) غير موجودة.

**الدليل الحقيقي**: فحصت الفرضية البديلة (rate limiter بند 22 يغطي
كده أصلًا؟) بقراءة مباشرة لـ`src/middlewares/rate_limiter.py`:

- `RateLimiterMiddleware` بيشتغل على **`process_request` بس** (مفيش
  `process_response`/`process_exception` خالص — موثّق صراحة في
  الملف نفسه). بيراقب **توقيت الطلبات الصادرة بينها وبعض** (inter-request
  timing عبر الدومين كله)، عبر `is_pattern_too_regular` (معامل تباين
  الفترات الزمنية). ده **مراقب/monitor**، مش **مولّد** — بيعتمد على
  "naturally jittered traffic (human timing, or Scrapy's own randomized
  delay)" (تعليق في الملف نفسه) كمصدر التذبذب، مش بيصنعه هو.
- `_scroll.py` عنده جزء بنائي جزئي حقيقي: `randomized_pause_ms` بموديل
  "fatigue" (متوسط الوقفة بيزيد تدريجيًا كل خطوة scroll) — لكن ده
  **محصور في وقفات السكرول جوه scroll loop واحد بس**، مش:
  - وقت عام على الصفحة قبل أي تفاعل (dwell time).
  - نمط clicks متسلسل واقعي.
  - فترات idle مش مرتبطة بالسكرول.
  - تبديل تابات (`visibilitychange`).

**الخلاصة**: الفجوة حقيقية ومحددة بدقة — مستوى مختلف تمامًا عن اللي
موجود (request-level cadence + scroll-step pause) عن اللي التقرير
بيتكلم عنه (session-level narrative behavior). التقرير صح، بعد
التصحيح في الاسم فقط.

### التصميم المقترح (test-environment فقط)

- endpoint جديد `GET /session-behavior-check`: صفحة عادية بتاعنا
  (زي `/` الموجودة) مع JS بيسجّل timeline كامل: `DOMContentLoaded`
  timestamp، أول `scroll`/`click`/`mousemove` timestamp (= "كام وقفة
  قبل أول تفاعل")، تكرار وتباعد الـscroll events، وقت `visibilitychange`
  (تبديل تاب).
- `POST /session-behavior-report`: بيحسب:
  - **وقت أول تفاعل** (صفر تقريبًا = بوت بيتفاعل فورًا، إنسان بياخد
    وقت "قراءة" حقيقي).
  - **انتظام الـscroll events** (نفس مبدأ CV اللي rate_limiter مستخدمه
    فعليًا، بس هنا على مستوى الصفحة الواحدة مش عبر الطلبات).
  - غياب تام لأي `visibilitychange` = دايمًا في تاب واحد (مش دليل قاطع
    لوحده، لكن إشارة).
- **إعادة استخدام**: منطق CV (`is_pattern_too_regular`) من
  `rate_limiter.py` قابل لإعادة استخدام مباشر هنا (نفس الحساب
  الإحصائي، نطاق بيانات مختلف) — مش لازم يتكتب من الأول.

---

## ملخص نقاط الإجماع بين البنود (لو التنفيذ يتقرر)

كل الفحوصات الجديدة المقترحة (1، 2، 5، 7 + توسعة 3) بتتبع **نفس النمط
المعماري الموجود بالفعل** في `security/botd_integration.py`/
`fpscanner_integration.py`:

1. سكريبت JS بسيط، كودنا احنا (مش vendored)، في صفحة mock-target
   موجودة أو جديدة.
2. `POST` endpoint جديد يستقبل الـreport.
3. دالة **نقية** (pure function) تحسب/تسجّل النتيجة — قابلة للاختبار
   بدون متصفح حقيقي (unit tests عادية).
4. **log-only دايمًا** — INFO، صفر إنفاذ، صفر عتبة WARNING تلقائية —
   نفس مبدأ Microsoft/F5 الموثّق في بند 19: سجّل الأول، قرر لاحقًا بعد
   دراسة بيانات حقيقية.
5. `config.py`: flag تفعيل + مسار log، نفس نمط `ja4_log_path`/
   `fingerprint_log_path`.
6. اختبار حي (`tests/integration/`) يثبت الـendpoint شغّال ضد Camoufox/
   Patchright حقيقيين، مش افتراض.

**التنفيذ الفعلي لأي بند من دول محتاج موافقة صريحة منفصلة — المستند ده
جرد وتخطيط بس.**
