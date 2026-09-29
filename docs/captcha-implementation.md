# 图片与邮箱验证码说明

注册链路使用两次独立的图片验证码：第一次用于申请邮箱验证码，第二次用于最终注册。验证码均存储在 MySQL 的 `nlp_auth_codes` 表中，经过哈希处理、两分钟过期且只能消费一次。

## API

### `GET /api/v1/auth/captcha`

生成四位图片验证码（后端画布 200×80，前端以 160×64 展示），返回验证码 ID 和 Base64 PNG：

```json
{
  "captcha_id": "uuid",
  "image": "data:image/png;base64,..."
}
```

### `POST /api/v1/auth/email/send`

验证第一张图片后，通过配置的邮件 provider 发送六位邮箱验证码：

```json
{
  "email": "user@example.com",
  "captcha_id": "uuid",
  "captcha_code": "ABCD"
}
```

成功响应：

```json
{"message": "Email code sent successfully"}
```

接口按邮箱和客户端 IP 频控。邮箱验证码两分钟过期且只能使用一次。

### `POST /api/v1/auth/register`

```json
{
  "email": "user@example.com",
  "email_code": "519760",
  "password": "test123456",
  "display_name": "TestUser",
  "captcha_id": "uuid",
  "captcha_code": "XYZW"
}
```

后端会校验第二张图片验证码与邮箱验证码，检查邮箱是否重复，然后创建用户、个人工作空间和默认 `guest` 角色。

## 邮件 provider 配置

默认 provider 是 `smtp`，用于保持现有开发环境兼容。生产环境可切换到腾讯云 SES：

```dotenv
NLP_AGENT_EMAIL_PROVIDER=tencent_ses
NLP_AGENT_TENCENT_SES_SECRET_ID=your-secret-id
NLP_AGENT_TENCENT_SES_SECRET_KEY=your-secret-key
NLP_AGENT_TENCENT_SES_REGION=ap-hongkong
NLP_AGENT_TENCENT_SES_FROM=noreply@mail.lsnunlp.com
NLP_AGENT_TENCENT_SES_FROM_NAME=LSNU NLP
NLP_AGENT_TENCENT_SES_TEMPLATE_ID=218769
NLP_AGENT_TENCENT_SES_SUBJECT=approved-template-subject
```

`ap-hongkong` 是腾讯云 SES 的服务 API 地域，与应用服务器所在城市无关；成都服务器也应使用 SES 身份所在的地域。腾讯 SES 模板中的 `{{code}}` 会通过 `TemplateData` 传入。

腾讯 SES 的发信模板页面不单独设置邮件主题，但 `SendEmail` 请求仍要求 `Subject` 字段。因此 `NLP_AGENT_TENCENT_SES_SUBJECT` 是后端发送请求时使用的邮件标题；它不会作为模板变量，也不需要在模板正文中配置。

### SMTP 配置

本地运行时可在项目根目录 `.env` 设置：

```dotenv
NLP_AGENT_SMTP_HOST=smtp.example.com
NLP_AGENT_SMTP_PORT=465
NLP_AGENT_SMTP_SECURITY=ssl
NLP_AGENT_SMTP_USER=mailer@example.com
NLP_AGENT_SMTP_PASSWORD=authorization-code
NLP_AGENT_SMTP_FROM=mailer@example.com
```

`NLP_AGENT_SMTP_SECURITY` 只允许 `ssl`、`starttls` 或 `none`。需要回滚或本地继续使用 SMTP 时，将 `NLP_AGENT_EMAIL_PROVIDER` 设为 `smtp`。生产环境必须配置真实 SMTP 或腾讯 SES；仅本地开发可显式设置 `NLP_AGENT_EMAIL_DEVELOPMENT_MODE=true` 跳过发送。若还需在开发日志中看到验证码，必须额外设置 `NLP_AGENT_EMAIL_EXPOSE_CODE=true`，不要在生产环境启用。

## 前端流程

1. 输入邮箱和第一张图片验证码。
2. 点击“发送验证码”，邮件发送成功后加载第二张图片验证码。
3. 输入邮件中的验证码、密码和第二张图片验证码后提交注册。
4. 修改邮箱会清空此前发送状态和第二张验证码，避免把旧邮箱验证码用于新地址。
5. 图片加载失败时可点击刷新按钮重试。

## 故障排查

- 图片验证码不可见：检查 `GET /api/v1/auth/captcha`，然后点击刷新。
- 邮件接口返回 503：检查 provider 选择、腾讯 SES 必填项（包括 API 地域/模板 ID/主题）或 SMTP 必填项，确认应用读取的是项目根目录 `.env`。
- 收不到邮件：检查腾讯 SES 模板审核状态、发信地址/域名、收件地址垃圾邮件目录和后端日志；使用 SMTP 时检查授权码。
- 验证码错误：验证码可能输入错误、超过两分钟或已经被消费，重新获取即可。

## 相关文件

- `server/auth/captcha.py`
- `server/auth/code_store.py`
- `server/user/email_provider.py`
- `server/web/app.py`
- `server/user/schemas.py`
- `webui/src/platform/http/api.ts`
- `webui/src/modules/auth/LoginPage.tsx`
- `webui/src/modules/student/components/LoginDialog.tsx`
