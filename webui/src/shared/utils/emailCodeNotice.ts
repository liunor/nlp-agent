export function emailCodeNotice(expiresIn?: number): string {
  if (!expiresIn || !Number.isFinite(expiresIn) || expiresIn <= 0) {
    return "验证码已提交发送，请尽快查收。";
  }
  const duration = expiresIn % 60 === 0 ? `${expiresIn / 60} 分钟` : `${Math.ceil(expiresIn)} 秒`;
  return `验证码已提交发送，有效期 ${duration}。邮件迟到或验证码过期时，请重新获取。`;
}
