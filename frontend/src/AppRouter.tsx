import { StrictMode } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import './index.css';
import AppLayout from './AppLayout';
import DashboardPage from './DashboardPage';
import LoginPage from './LoginPage';
import PlaceholderPage from './PlaceholderPage';
import { AuthProvider, RequireAuth, RequireRole } from './auth';

function AppRouter() {
  return (
    <BrowserRouter>
      <StrictMode>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route
            element={
              <AuthProvider>
                <RequireAuth />
              </AuthProvider>
            }
          >
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route
                path="sales"
                element={
                  <PlaceholderPage
                    title="Sales"
                    description="Ring up sales and review completed transactions."
                  />
                }
              />
              <Route
                path="sales-manager"
                element={
                  <PlaceholderPage
                    title="Sales Manager"
                    description="Manage open sales, line voids, and sale corrections."
                  />
                }
              />
              <Route
                path="customers"
                element={
                  <PlaceholderPage
                    title="Customers"
                    description="Manage customer accounts, balances, and credit limits."
                  />
                }
              />
              <Route
                path="suppliers"
                element={
                  <PlaceholderPage
                    title="Suppliers"
                    description="Manage supplier contacts and their linked products."
                  />
                }
              />
              <Route
                path="inventory"
                element={
                  <PlaceholderPage
                    title="Inventory"
                    description="Track stock movements, receive stock, and watch low-stock items."
                  />
                }
              />
              <Route
                path="close"
                element={
                  <PlaceholderPage
                    title="Close"
                    description="Count the drawer and close the current session with a Z-report."
                  />
                }
              />

              <Route element={<RequireRole roles={['manager', 'admin']} />}>
                <Route
                  path="users"
                  element={
                    <PlaceholderPage
                      title="Users"
                      description="Create and manage cashier, manager, and admin accounts."
                    />
                  }
                />
                <Route
                  path="settings"
                  element={
                    <PlaceholderPage
                      title="Settings"
                      description="Configure store details, backup path, and preferences."
                    />
                  }
                />
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Route>
        </Routes>
      </StrictMode>
    </BrowserRouter>
  );
}

export default AppRouter;
