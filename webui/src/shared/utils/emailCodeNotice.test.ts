import { emailCodeNotice } from './emailCodeNotice';

it('uses the duration returned by the server and distinguishes late mail', () => {
  expect(emailCodeNotice(300)).toContain('有效期 5 分钟');
  expect(emailCodeNotice(120)).toContain('有效期 2 分钟');
  expect(emailCodeNotice(90)).toContain('有效期 90 秒');
  expect(emailCodeNotice(300)).toContain('邮件迟到');
});

it('does not invent a duration when an older server omits it', () => {
  expect(emailCodeNotice()).not.toContain('分钟');
  expect(emailCodeNotice(Number.NaN)).not.toContain('分钟');
});
