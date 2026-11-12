import { Icon } from './Icon';

interface PlaceholderPageProps {
  title: string;
  description: string;
}

export default function PlaceholderPage({
  title,
  description,
}: PlaceholderPageProps) {
  return (
    <div className="mm-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">{title}</h1>
          <p className="mm-page-subtitle">{description}</p>
        </div>
      </header>

      <section className="mm-card mm-empty">
        <span className="mm-icon-tile">
          <Icon name="sparkles" size={22} />
        </span>
        <h2 className="mm-empty-title">Nothing here yet</h2>
        <p className="mm-empty-text">
          This area is part of the next round of updates. Your existing data
          and workflows are unchanged.
        </p>
      </section>
    </div>
  );
}
