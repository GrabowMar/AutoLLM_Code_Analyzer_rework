// CANARY FIXTURE: deliberately vulnerable React + TypeScript component (analyzer self-test).
// Icon is used only as a JSX element and must NOT be reported as unused.
interface BannerProps {
  html: string;
}

function Icon() {
  return <span aria-hidden="true">!</span>;
}

export default function Banner({ html }: BannerProps) {
  const unusedLabel: string = "banner";
  return (
    <div className="banner">
      <Icon />
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
