export default function BookmarkList({ items }) {
  if (items.length === 0) {
    return <p>No bookmarks yet.</p>;
  }
  return (
    <ul>
      {items.map((item) => (
        <li key={item.id}>
          <a href={item.url} rel="noreferrer" target="_blank">
            {item.title}
          </a>
        </li>
      ))}
    </ul>
  );
}
