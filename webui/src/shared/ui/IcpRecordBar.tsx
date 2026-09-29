import policeRecordIcon from "../../../logo/备案图标.png";

const MIIT_RECORD_URL = "https://beian.miit.gov.cn/";
const POLICE_RECORD_URL =
  "https://beian.mps.gov.cn/#/query/webSearch?code=51110202002432";

export function IcpRecordBar() {
  return (
    <div className="site-icp-bar" role="contentinfo">
      <span>© 2026 乐山师范学院自然语言处理教学平台</span>
      <span aria-hidden="true">·</span>
      <a href={MIIT_RECORD_URL} target="_blank" rel="noreferrer">
        蜀ICP备2026055638号
      </a>
      <span aria-hidden="true">·</span>
      <a
        className="site-police-record-link"
        href={POLICE_RECORD_URL}
        target="_blank"
        rel="noreferrer"
      >
        <img src={policeRecordIcon} alt="" />
        <span>川公网安备51110202002432号</span>
      </a>
    </div>
  );
}
