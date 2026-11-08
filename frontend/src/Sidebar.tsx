import { useState } from 'react';

function Sidebar() {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <nav className="sidebar" style={{ 
      width: '250px', 
      position: 'fixed', 
      top: 0, 
      left: 0, 
      height: '100vh', 
      backgroundColor: '#f5f5f5', 
      padding: '1rem',
      boxShadow: '2px 0 5px rgba(0,0,0,0.1)'
    }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          position: 'absolute',
          top: '1rem',
          left: isOpen ? '0' : '260px',
          background: 'none',
          border: 'none',
          fontSize: '1.5rem',
          cursor: 'pointer',
          zIndex: 1000
        }}
      >
        {isOpen ? '☰' : '⎔'}
      </button>

      <div style={{ marginTop: '2rem', padding: '0 1rem' }}>
        <h3>Navigation</h3>
        <div>
          <a href="#" className="nav-link" onClick={() => window.history.go(0)}>
            Home
          </a>
        </div>
      </div>
    </nav>
  );
}

export default Sidebar;