export const Note = ({ html }: { html: string }) => <div dangerouslySetInnerHTML={{ __html: html }} />;
