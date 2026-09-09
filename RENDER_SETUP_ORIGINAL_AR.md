# إعداد نشر النسخة الأصلية على Render

هذه الوثيقة مخصصة للفرع `uploaded/original-sanitized-archive` من مشروع Telegram Media Suite. لا تحتوي على أي قيمة سرية.

## إعداد الخدمة

أنشئ خدمة من نوع **Web Service** من المستودع الخاص، واختر الفرع:

```text
uploaded/original-sanitized-archive
```

استخدم الإعدادات التالية:

| الحقل | القيمة |
|---|---|
| Runtime | Python 3 |
| Region | أي منطقة مناسبة، ويفضل المنطقة نفسها للخدمات المرتبطة |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python3 scripts/render_start.py` |
| Health Check Path | `/health` |
| Root Directory | اتركه فارغًا |
| Plan | Free إن كان متاحًا في حساب Render |

## متغيرات البيئة

أضف الأسماء التالية في Render، ثم ضع القيم السرية من ملف البيئة السابق في الحقول المقابلة. لا تضع ملف `.env` داخل المستودع.

| الاسم | الملاحظة |
|---|---|
| `API_ID` | معرّف Telegram API |
| `API_HASH` | قيمة Telegram API السرية |
| `BOT_TOKEN` | رمز بوت Telegram |
| `PHONE` | رقم الحساب بصيغة دولية |
| `ALLOWED_USER_IDS` | معرفات المستخدمين المسموح لهم |
| `TMD_SESSION_SETUP_OWNER_ID` | معرف مالك إعداد الجلسة |
| `TMD_SESSION_B64` | جلسة Telegram إن كانت متاحة |
| `TMD_SESSION_IMPORT_MODE` | اتركها `preserve`؛ استخدم `replace` فقط لاستبدال جلسة موجودة عمدًا |
| `TMD_DASHBOARD_TOKEN` | رمز حماية لوحة الإدارة |
| `TMD_SUITE_DATA` | `/opt/render/project/src/.tmd-data` |
| `TMD_HOST` | `0.0.0.0` |
| `TMD_OPEN_BROWSER` | `false` |
| `AUTO_START` | `true` |
| `SELF_ENROLLMENT_ENABLED` | `false` |
| `PYTHON_VERSION` | `3.12.4` |

## لوحة تشغيل الجلسة

بعد النشر، استخدم:

```text
https://SERVICE-NAME.onrender.com/
```

أو افتح مسار الفحص:

```text
https://SERVICE-NAME.onrender.com/health
```

تُستخدم لوحة الإدارة لطلب رقم الهاتف، إدخال رمز Telegram، إدخال كلمة مرور التحقق بخطوتين عند الحاجة، ومتابعة حالة الجلسة. لا ترسل الرمز أو كلمة المرور إلى GitHub أو إلى سجلات Render.

## ملاحظات مهمة

إذا لم تكن `TMD_SESSION_B64` صالحة، ستظهر الجلسة في حالة `code`، ويجب إكمال المصادقة من لوحة الإدارة. في لوحة الإدارة يوجد خيار **ترحيل الجلسة إلى Render** لتنزيل ملف `tmd_user.session.b64`، ثم يُنسخ محتواه إلى `TMD_SESSION_B64`. الوضع الافتراضي `preserve` لا يستبدل جلسة موجودة عند إعادة التشغيل أو النشر؛ ولا تُحذف الجلسة إلا عبر خيار **إزالة جلسة Render وبدء جديدة** وبعد تأكيد صريح.

خطأ `AccessTokenExpired` يخص `BOT_TOKEN` المنتهي أو الملغى، وليس جلسة الحساب الشخصي. عند ظهوره حدّث `BOT_TOKEN` من إعدادات البوت فقط، واترك `TMD_SESSION_B64` والجلسة كما هما.

يجب ضبط خدمة المراقبة الخارجية على طلب:

```text
https://SERVICE-NAME.onrender.com/health
```

بفاصل لا يقل عن 15 دقيقة إذا كانت الخدمة الخارجية تسمح بذلك. لا تضع أي رمز Telegram أو كلمة مرور التحقق في رابط المراقبة أو في إعداداتها.
