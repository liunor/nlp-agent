# 图片验证码功能说明

## ✅ 已完成的功能

### 1. 后端 API 端点

#### GET /api/v1/auth/captcha
- **功能**: 生成图片验证码
- **返回格式**: 
  ```json
  {
    "captcha_id": "uuid字符串",
    "image": "data:image/png;base64,..."
  }
  ```
- **特性**:
  - 生成 4 位随机字符（大写字母 + 数字，排除易混淆字符 O/0/I/1/L）
  - 图片尺寸: 160x60 像素
  - 包含干扰线和噪点防止 OCR 识别
  - 有效期: 2 分钟
  - 一次性使用（验证后自动删除）

#### POST /api/v1/auth/sms/send
- **功能**: 发送短信验证码（需先验证图片验证码）
- **请求体**:
  ```json
  {
    "phone_number": "手机号",
    "captcha_id": "图片验证码ID",
    "captcha_code": "用户输入的验证码"
  }
  ```
- **响应**: `{"message": "SMS code sent successfully"}`
- **验证逻辑**:
  - 首先验证图片验证码是否正确
  - 错误则返回 HTTP 400
  - 正确则生成 6 位随机短信验证码

#### POST /api/v1/auth/register
- **功能**: 用户注册（需验证图片验证码和短信验证码）
- **请求体**:
  ```json
  {
    "phone_number": "手机号",
    "sms_code": "短信验证码",
    "password": "密码（至少8位）",
    "display_name": "显示名称（可选）",
    "captcha_id": "图片验证码ID",
    "captcha_code": "用户输入的验证码"
  }
  ```
- **响应**: 
  ```json
  {
    "message": "User registered successfully",
    "phone_number": "...",
    "display_name": "..."
  }
  ```
- **验证逻辑**:
  - 验证图片验证码
  - TODO: 验证短信验证码
  - TODO: 检查用户是否已存在
  - TODO: 创建用户记录

### 2. 前端实现

#### LoginPage.tsx
- **位置**: `webui/src/modules/auth/LoginPage.tsx`
- **功能**:
  - 页面加载时自动获取图片验证码
  - 显示 Base64 PNG 图片
  - 支持点击刷新验证码
  - 发送短信前验证图片验证码
  - 注册时再次验证图片验证码

#### api.ts
- **位置**: `webui/src/platform/http/api.ts`
- **API 方法**:
  - `getCaptcha()`: 获取验证码
  - `sendSmsCode(phoneNumber, captchaId, captchaCode)`: 发送短信
  - `register(data)`: 用户注册

### 3. 验证码生成逻辑

#### server/auth/captcha.py
- **generate_captcha_image()**:
  - 生成 4 位随机字符
  - 使用 PIL/Pillow 渲染图片
  - 添加随机旋转、位置偏移
  - 添加 6 条干扰线
  - 添加 80 个噪点
  - 返回 Base64 PNG 和 UUID
  
- **verify_captcha(captcha_id, code)**:
  - 不区分大小写比较
  - 一次性使用（验证后删除）
  - 检查过期时间（2 分钟 TTL）
  - 线程安全的内存存储

## 📋 使用流程

### 注册新用户

1. **打开注册页面**
   - 访问 `http://127.0.0.1:5173/login`
   - 切换到"注册新账户"标签

2. **输入手机号**
   - 在"手机号"输入框中输入你的手机号

3. **查看并输入图片验证码**
   - 系统自动生成图片验证码
   - 图片显示 4 个字符（带干扰线和噪点）
   - 在"图片验证码"输入框中输入看到的 4 个字符
   - 如果看不清，点击图片或刷新按钮获取新的验证码

4. **发送短信验证码**
   - 点击"发送验证码"按钮
   - 系统验证图片验证码
   - 如果正确，生成 6 位短信验证码
   - **开发环境**: 查看后端控制台输出 `[SMS] Sending code XXXXXX to ...`
   - **生产环境**: 实际发送短信到手机

5. **输入短信验证码**
   - 在"短信验证码"输入框中输入收到的 6 位数字
   - 倒计时 60 秒内可以重新发送

6. **填写密码**
   - 输入密码（至少 8 位）
   - 确认密码（必须与密码一致）

7. **填写显示名称（可选）**
   - 输入你的昵称

8. **再次输入图片验证码**
   - 系统生成新的图片验证码用于注册验证
   - 在"注册验证"区域输入看到的 4 个字符

9. **提交注册**
   - 点击"注册"按钮
   - 系统验证所有信息
   - 成功后自动登录或跳转到登录页

## ️ 开发环境注意事项

### 当前实现状态
- ✅ 图片验证码完整实现
- ✅ 图片验证码验证逻辑完整
- ️ 短信发送为模拟实现（仅打印到控制台）
- ⚠️ 用户注册为占位符实现（未真正创建数据库记录）

### 如何测试
1. 启动后端服务: `.venv\Scripts\python.exe -m uvicorn server.web.app:app --reload`
2. 启动前端服务: `npm run dev`
3. 打开浏览器: `http://127.0.0.1:5173/login`
4. 切换到注册标签
5. 输入手机号和图片验证码
6. 点击"发送验证码"
7. **查看后端控制台**，找到类似 `[SMS] Sending code 519760 to 18881356323` 的输出
8. 使用显示的 6 位数字作为短信验证码
9. 完成注册流程

### 生产环境待实现
1. **SMS 网关集成**
   - 阿里云短信服务
   - Twilio
   - 或其他 SMS 提供商
   
2. **验证码存储**
   - 使用 Redis 存储短信验证码
   - 设置 TTL（如 5 分钟）
   - 限制发送频率（防刷）

3. **用户创建**
   - 密码 Argon2 哈希
   - 数据库插入操作
   - 默认角色分配（如 "student"）
   - 工作空间关联

4. **安全增强**
   - IP 限流
   - 手机号格式验证
   - 重复注册检测
   - 验证码重试次数限制

## 🔍 故障排查

### 问题: 看不到图片验证码
**原因**: 后端 `/api/v1/auth/captcha` 端点未实现  
**解决**: ✅ 已修复，重启后端服务即可

### 问题: 发送验证码返回 "Not Found"
**原因**: 后端 `/api/v1/auth/sms/send` 端点未实现  
**解决**: ✅ 已修复，重启后端服务即可

### 问题: 注册返回 "Not Found"
**原因**: 后端 `/api/v1/auth/register` 端点未实现  
**解决**: ✅ 已修复，重启后端服务即可

### 问题: 验证码总是提示错误
**可能原因**:
1. 输入了错误的验证码字符
2. 验证码已过期（超过 2 分钟）
3. 验证码已被使用（一次性）

**解决方法**:
- 仔细查看图片中的字符（注意区分 0/O, 1/I/L）
- 点击图片刷新获取新的验证码
- 尽快输入并提交

### 问题: 收不到短信验证码
**开发环境**: 查看后端控制台输出  
**生产环境**: 检查 SMS 网关配置和日志

## 📊 API 测试示例

### 获取验证码
```bash
curl http://127.0.0.1:8765/api/v1/auth/captcha
```

返回:
```json
{
  "captcha_id": "f2b5fe156faf407197dee747380bcd52",
  "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAKAA..."
}
```

### 发送短信
```bash
curl -X POST http://127.0.0.1:8765/api/v1/auth/sms/send \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "18881356323",
    "captcha_id": "f2b5fe156faf407197dee747380bcd52",
    "captcha_code": "DYJE"
  }'
```

成功返回:
```json
{
  "message": "SMS code sent successfully"
}
```

失败返回（验证码错误）:
```json
{
  "detail": "Invalid or expired CAPTCHA"
}
```

### 用户注册
```bash
curl -X POST http://127.0.0.1:8765/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "18881356323",
    "sms_code": "519760",
    "password": "test123456",
    "display_name": "TestUser",
    "captcha_id": "abc123...",
    "captcha_code": "XYZW"
  }'
```

## 🎯 下一步优化建议

1. **完善 SMS 集成** - 接入真实的短信服务商
2. **添加 Redis 缓存** - 存储短信验证码，提高性能
3. **实现完整的用户创建** - 包括密码哈希、数据库操作等
4. **添加限流机制** - 防止恶意刷验证码
5. **增加验证码难度选项** - 可配置字符数量、干扰程度等
6. **添加无障碍支持** - 提供语音验证码选项
7. **监控和日志** - 记录验证码使用情况，便于审计

## 📝 相关文件

- 后端验证码逻辑: `server/auth/captcha.py`
- 后端 API 路由: `server/web/app.py` (第 481-623 行)
- 前端 API 调用: `webui/src/platform/http/api.ts` (第 212-223 行)
- 前端登录页面: `webui/src/modules/auth/LoginPage.tsx` (第 104-284 行)
- Schema 定义: `server/user/schemas.py` (第 87-104 行)
