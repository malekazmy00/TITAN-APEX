# خريطة العقبات الشاملة وجدول التصعيد (Comprehensive Obstacle Map)

> الهدف: تغطية كل العقبات المحتملة في 8 محاور، مبنية على أبحاث فعلية (2026)
> وليس تخمين. كل عقبة موسومة بحالتها الحالية في مشروعنا، ومُجمّعة في
> "مستويات مركّبة" لأن العقبات الحقيقية نادرًا ما تظهر منفردة.

---

## المحور 1 — الشبكة والبنية التحتية (Network/Infra)

| العقبة | الوصف | حالتنا |
|---|---|---|
| IP hard/soft ban | حظر كامل، أو محتوى مزيف بدل حظر ظاهر | ⬜ غير مُختبر |
| Rate limiting تصاعدي | 429 مع `Retry-After` متزايد | ✅ (Round 2، مبدئي) |
| Geofencing | محتوى مختلف حسب IP/الدولة | ⬜ غير مُختبر |
| أخطاء CDN/edge (520, 521, 522) | استجابة upstream غير صالحة | ⬜ غير مُختبر |
| Timeouts متعددة الأنواع | connection/read/total timeout منفصلين | ⬜ (عندنا timeout عام بس) |
| Redirect chains طويلة/حلقية | إعادة توجيه لا نهائية أو مفرطة | ⬜ غير مُختبر |
| TLS/JA4 fingerprinting | بصمة بروتوكولية قبل حتى وصول الطلب للتطبيق | ⬜ غير مُختبر - **فجوة كبيرة** |
| Datacenter IP blocking | حظر تلقائي لأي IP معروف كـ cloud/hosting | ⬜ غير مُختبر (مرتبط بمرحلة الـ proxies المؤجلة) |

## المحور 2 — التحديات وanti-bot (Challenge Layer)

| العقبة | الوصف | حالتنا |
|---|---|---|
| CAPTCHA (reCAPTCHA/hCAPTCHA) | تحدي بصري/تفاعلي | ⬜ غير مُختبر |
| Proof-of-Work (Anubis-style) | تحدي حسابي | ✅ اتغلب عليه (Round 2/3) |
| WAF signature-based | فحص أنماط الطلبات | ⬜ (SafeLine مؤجل) |
| Behavioral biometrics | حركة ماوس/كتابة/scroll | ⬜ مسجّل كهدف بحثي (fpscanner+JA4) |
| ML bot scoring (Cloudflare-style) | نقاط تراكمية من كذا إشارة مع بعض | ⬜ غير قابل للمحاكاة الكاملة (مفتوح المصدر محدود هنا) |
| Honeypots (روابط/حقول مخفية) | فخاخ تكشف الأتمتة | ✅ Round 1 |
| Decoy/poisoned data | بيانات قديمة مخفية لتسميم النتائج | ✅ Round 1 |
| **AI Labyrinth (تسميم على نطاق واسع)** | Cloudflare بتولّد صفحات وهمية بالذكاء الاصطناعي كـ honeypot ضخم - السكرابر يفضل يزحف ويسحب بيانات قمامة من غير ما يلاحظ | ⬜ **فجوة جديدة مكتشفة من البحث - مش مجرد honeypot فردي، لكن شبكة كاملة من صفحات وهمية** |

## المحور 3 — البنية الهيكلية (Structural/DOM)

| العقبة | الوصف | حالتنا |
|---|---|---|
| Markup randomization | class names بتتغيّر دوريًا | ✅ Round 1 |
| Shadow DOM | محتوى معزول عن `document` العادي | ✅✅ **مُختبر ومحلول فعليًا بدليل CI** (docs/REQUIREMENTS.md قسم 9: بند 11 وثّق الفجوة الحقيقية عبر `page.content()`، بند 12 حلّها معماريًا بـ `extraction_mode: live_dom` — `page.locator()` بيخترق shadow roots المفتوحة تلقائيًا، مؤكد فعليًا CI run 32680454673: 11/11 items اترجعوا) — الفجوة الأصلية لسه موثّقة ومش ممسوحة (`mock_target_camoufox.yaml` لسه بيوضّحها لمين مايستخدمش `live_dom`) |
| Web Components مخصصة | عناصر بتتصرف زي widgets مش HTML عادي | ⬜ غير مُختبر |
| DOM Virtualization (قوائم افتراضية) | مش كل العناصر موجودة في الـ DOM في نفس اللحظة، حتى لو ظاهرة على الشاشة | ✅✅ **مُختبر ومحلول فعليًا بدليل CI** (docs/REQUIREMENTS.md قسم 9: بند 13 وثّق الفجوة الحقيقية عبر `parsed_html` و`live_dom` الاتنين معًا — CI run 32725098955، 5 items بس بدل 10؛ أول جولة يبان فيها إن `live_dom` (حل بند 12) مش حل عام: بيحل مشاكل *encapsulation* بس، مش مشاكل *وجود/توقيت*. بند 14 حلّها معماريًا بـ progressive scroll + incremental extraction — `scroll_and_collect` بيلقّط/يستخرج بعد كل خطوة scroll لوحدها (مش قراءة نهائية واحدة)، dedup بـ`post_id` عبر الـ crawl كله، مؤكد فعليًا CI run 32735734451: 25/25 items اترجعوا للاتنين extraction_mode بعد 3 مراجعات موثّقة كلها) — الفجوة الأصلية (بند 13's configs) لسه موثّقة ومش ممسوحة كـ regression sentinel (`mock_target_feed_virtualized_{parsed_html,live_dom}.yaml` لسه بيوضّحوا الفرق من غير `progressive_extraction: true`) |
| CSS-in-JS (hashed classes) | زي `styled-components` | ✅ اتسجل كـ Known Limitation #5 |
| Hydration-dependent layout | HTML أولي فاضي، المحتوى بيجيله بعدين | ✅ (Playwright render، مُختبر جزئيًا) |
| Multi-row/sibling items بدون container | زي `tableful` | ✅ Known Limitation #2 |
| Nested iframes | محتوى داخل iframe داخل iframe | ⬜ (مُختبر iframe واحد بس - `frames` page) |

## المحور 4 — المحتوى الديناميكي والتوقيت (Dynamic/Timing)

| العقبة | الوصف | حالتنا |
|---|---|---|
| Async delay بعد التحميل | زي `ajax_javascript` | ✅ (`render_wait_ms`) |
| Infinite scroll (Intersection Observer) | تحميل عند وصول العنصر لمنطقة الرؤية | ✅ Round 1/2 |
| Load-more click-triggered | زرار بدل scroll | ✅ (`click_selector`) |
| Race condition بين render وقراءة المحتوى | توقيت غير حتمي (flaky) | ✅ اتسجل واتصلح (`ajax_javascript`) |
| Real-time updates أثناء الزحف | البيانات بتتغيّر وإحنا بنقرأها | ✅ (Hacker News، مبدئي) |
| WebSocket-driven content | محتوى بيوصل عبر WebSocket مش HTTP عادي | ⬜ غير مُختبر - **فجوة** |

## المحور 5 — المصادقة والجلسات (Auth/Session)

| العقبة | الوصف | حالتنا |
|---|---|---|
| Login (POST + session) | تسجيل دخول أساسي | ✅✅ **مُختبر ومحلول فعليًا بدليل CI** (docs/REQUIREMENTS.md قسم 9: بند 15 — `/login` حقيقي (GET فورم + POST بيانات) على mock-target، `perform_login_and_navigate` بيملأ الفورم ويضغط submit فعليًا عبر camoufox/patchright، الـ session cookie بيتحمل تلقائيًا لأي طلب لاحق لنفس الـ target داخل نفس الـ `solve()`، مؤكد فعليًا CI run 32785461995: `test_login_flow_reaches_protected_data_after_a_real_post_and_csrf_token` PASSED — 5 items حقيقية بعد تسجيل دخول ناجح؛ ومحاولة بدون تسجيل دخول بترفض بوضوح 401/403 موثّق في اللوج مش crash، `test_feed_protected_without_any_login_yields_nothing_not_a_crash` PASSED) |
| CSRF token rotation | توكن بيتغيّر كل طلب | ✅✅ **مُختبر ومحلول فعليًا بدليل CI** (docs/REQUIREMENTS.md قسم 9: بند 15 — `CsrfTokenStore` بيولّد توكن عشوائي جديد كل `GET /login` وبيستهلكه (single-use حقيقي، مش مجرد "بيتغيّر مع كل تحميل") أول ما `POST` يستخدمه بنجاح؛ المتصفح نفسه بيقرأ ويبعت التوكن من الفورم الحقيقي، مش الكود بيبنيه، مؤكد فعليًا CI run 32785461995 (نفس الاختبار الحي أعلاه)) |
| 2FA/MFA | كود إضافي بعد كلمة السر | ⬜ غير مُختبر (خارج النطاق أخلاقيًا لمعظم الحالات) — مؤجّل صراحة لـ PHASE_2_BACKLOG لو احتجناه |
| OAuth/SSO redirects | تفويض عبر جهة خارجية | ⬜ غير مُختبر |
| Session expiry + إعادة مصادقة تلقائية | كشف انتهاء الجلسة والتجديد | 🟡 **اكتشاف فقط اتحل ومؤكد بدليل CI** — التجديد التلقائي (auto re-login) مؤجّل صراحة بطلب المستخدم لبند لاحق. docs/REQUIREMENTS.md قسم 9 بند 15: مسار اختباري حتمي `/test-expire-session` (زي `/honeypot-trap` بالظبط) بيلغي الجلسة عمدًا بين تسجيل الدخول والوصول للهدف الحقيقي، `log_login_outcome` بيسجّل `session_expired_mid_crawl` بوضوح في اللوج بدل فشل صامت أو بيانات فاضية من غير تفسير، مؤكد فعليًا CI run 32785461995: `test_session_expired_mid_crawl_after_a_real_login_yields_nothing_not_a_crash` PASSED |
| Session tied to fingerprint/IP | الجلسة بترفض لو الـ IP أو الـ UA اتغيّر | ⬜ غير مُختبر |

## المحور 6 — محتوى غير متوقع (Unexpected UX)

| العقبة | الوصف | حالتنا |
|---|---|---|
| Cookie consent banners/walls | overlay بيمنع المحتوى لحد الموافقة | ⬜ غير مُختبر - **شائع جدًا وسهل الإضافة** |
| Interstitials (إعلانات كاملة الشاشة) | تظهر بعد وقت/scroll معين | 🟡 **مبني ومُختبر محليًا، لسه محتاج تأكيد CI حي** (docs/REQUIREMENTS.md قسم 9: بند 16 — `/feed-interstitial` overlay حقيقي بيظهر بعد التحميل (time أو scroll، قابل للتهيئة، الاتنين مُنفّذين) وبيمنع تحميل بيانات إضافية فعليًا (JS flag، مش CSS overflow) لحد ما يتقفل بـ`click_selector` — نفس آلية cookie wall بالحرف. النتيجة النهائية (CI run حقيقي) هتتسجّل هنا بمجرد ما تتأكّد) |
| Modals عشوائية التوقيت | تظهر بشكل غير متوقع أثناء التصفح | ⬜ غير مُختبر |
| A/B test variants | نفس الصفحة بهيكل مختلف حسب المستخدم | ⬜ غير مُختبر - **فجوة مهمة، بتكسر الـ selectors بدون سبب ظاهري** |
| Maintenance/soft-404 pages | صفحة "تحت الصيانة" بدل 404 حقيقي | ⬜ غير مُختبر |
| Paywalls جزئية | جزء من المحتوى ظاهر والباقي محجوب | ⬜ غير مُختبر |

## المحور 7 — جودة البيانات (Data Quality)

| العقبة | الوصف | حالتنا |
|---|---|---|
| مشاكل الترميز (encoding) | UTF-8 مقابل ترميزات أخرى، رموز مكسورة | ⬜ غير مُختبر |
| محتوى مكرر/شبه مكرر | نفس البيانات بصياغات مختلفة | ⬜ غير مُختبر |
| اختلاف حسب اللغة/المنطقة (i18n) | selectors مش قابلة للنقل بين لغات | ⬜ غير مُختبر |
| Placeholder/loading content يتقرأ كبيانات حقيقية | نص "جاري التحميل..." يتسحب بالغلط | ⬜ غير مُختبر - **فجوة بسيطة الإضافة، عالية القيمة** |

## المحور 8 — التشغيل على نطاق واسع (Scale/Operational)

| العقبة | الوصف | حالتنا |
|---|---|---|
| Browser pool memory leaks | تراكم عمليات متصفح غير مغلقة | ⬜ غير مُختبر (خارج نطاق بيئة الاختبار الحالية) |
| Queue starvation | مهام عالقة بسبب اعتماديات دائرية | ⬜ غير مُختبر |
| Session pinning عبر crawl طويل | نفس الهوية لازم تفضل ثابتة لمدة الجلسة | ⬜ غير مُختبر |
| Distributed crawl consistency | نتائج متضاربة بين instances مختلفة | ⬜ خارج النطاق حاليًا (single-node) |

---

## جدول التصعيد المقترح (Escalation Schedule)

بدل ما نقفز لمستوى F5 على طول (قرار خطأ زي ما لاحظت بحق)، الترتيب الأصح يعتمد على **قيمة الاكتشاف** مقابل **تكلفة البناء**:

### المرحلة القادمة (Round التالي) — الأرخص بناءً + الأعلى قيمة اكتشاف
مش تصعيد "صعوبة حماية"، لكن تصعيد "واقعية هيكلية" - وده يشمل بالظبط اللي كنت بتسأل عنه (scroll غير متوقع، صفحات غير متوقعة، إعلانات):
1. **Cookie consent wall** (محور 6) - overlay حقيقي، سهل البناء، شائع جدًا في الواقع
2. **A/B test variants** (محور 6) - نفس endpoint، هيكل عشوائي بين طلبين متتاليين
3. **Placeholder content leakage** (محور 7) - نص "loading..." ظاهر لو التوقيت غلط
4. **JSON/API parsing support** (اللي كنا متفقين عليه أصلاً) - `/api/feed` + GenericSpider يقدر يتعامل مع JSON

### الجولة اللي بعدها — البنية المعقدة
5. Shadow DOM component واحد
6. DOM Virtualization بسيط (قائمة بتحمل/تشيل عناصر عند الـ scroll)
7. Interstitial بعد N ثانية/scroll

### الجولة اللي بعدها — المصادقة (لو قررتوا تفعّلوا Known Limitation #1)
8. Login كامل (session + CSRF)
9. Session expiry detection

### أبعد مرحلة — الطبقة السلوكية (F5-class)
10. fpscanner + JA4 + mouse telemetry (زي ما اتفقنا واتسجل بالفعل)

---

## أهم نقطة من كل البحث — العقبات المركّبة (Compound Scenarios)

كل المصادر البحثية (خصوصًا مصادر الدفاع الأمني نفسها) بتأكد على مبدأ واحد:
**"دفاع طبقة واحدة سهل تجاوزه - القوة الحقيقية في تراكم طبقات مختلفة مع بعض، ومفيش نظام دفاعي حقيقي بيعتمد على عقبة واحدة."**

يعني الاختبار الحقيقي مش "هل عدّينا Shadow DOM؟" و"هل عدّينا A/B testing؟" منفصلين - لكن **هل عدّينا الاتنين مع بعض في نفس الصفحة**، زي ما بيحصل فعليًا. أقوى سيناريو اختبار مركّب ممكن نبنيه دلوقتي (من غير ما نحتاج بناء جديد كبير) هو:

> صفحة فيها cookie wall، وبعد الموافقة، محتوى ديناميكي بـ A/B variant عشوائي، جزء منه Shadow DOM، وفيه honeypot مخفي، وSelectors بتتغيّر كل 15 دقيقة (عندنا بالفعل) - كل ده في نفس الطلب، مش طلبات منفصلة.

هيكل الـ config-driven عندنا (`SpiderConfig` + flags منفصلة لكل ميزة) مصمم أصلاً يسمح بتفعيل عدة طبقات مع بعض على نفس target - يعني التركيب ده ممكن من غير أي تغيير معماري، بس محتاج نبني المكونات الفردية الأول.

**تحديث (2026-08-24):** أربعة من الخمسة عناصر في السيناريو المركّب فوق
(cookie wall، A/B variant، honeypot، markup randomizer) اتأكّدوا فعليًا
شغّالين مع بعض في اختبار حقيقي واحد (docs/REQUIREMENTS.md قسم 9 بند
10، test-environment/CHANGELOG.md). **العنصر الخامس، Shadow DOM، اتضاف
وشُغّل فعليًا فوق نفس الأربعة، ونتيجته اتأكّدت بدليل CI حقيقي (بند 11،
CI run 32678444498)** — بس النتيجة مختلفة نوعيًا عن الأربعة اللي فاتوا:
مش نجاح، لكن **Known Limitation حقيقية ومؤكدة**: Camoufox لسه بيعدّي
الأربعة الأولانيين بنجاح على نفس الصفحة، بس مايوصلش لمحتوى الـ Shadow
DOM خالص (فجوة معمارية، مش توقيتية) — دليل حتمي قابل للتكرار (6 items
بالظبط بدل 10). السيناريو المركّب الخماسي الكامل (docs's own framing
فوق) دلوقتي مُختبر بالكامل، بجزء منه (4/5) بينجح وجزء منه (Shadow DOM)
موثّق كفجوة حقيقية غير مُصلحة عن قصد هذه الجولة.

---

## توصية الترتيب النهائي

1. أول 4 بنود في "المرحلة القادمة" (تكلفة بناء منخفضة، غطاء واقعي عالي)
2. تفعيل **تركيبة مركّبة واحدة** من البنود دي مع بعض على نفس mock target (اختبار حقيقي للتكامل، مش لكل ميزة لوحدها)
3. باقي الجدول بالترتيب المذكور، كل مرحلة توثّق في `test-environment/CHANGELOG.md` زي المعتاد
