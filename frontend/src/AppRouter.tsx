import { StrictMode } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import AppLayout from './AppLayout';
import DashboardPage from './DashboardPage';
import LoginPage from './LoginPage';
import PlaceholderPage from './PlaceholderPage';
import CustomersPage from './CustomersPage';
import UsersPage from './UsersPage';
import SalesPage from './SalesPage';
import { RequireOpenSession } from './RequireOpenSession';
import InventoryPage from './InventoryPage';
import ProductPage from './ProductPage';
import SettingsPage from './SettingsPage';
import ClosePage from './ClosePage';
import { AuthProvider, RequireAuth, RequireForcedPasswordChange, RequireRole } from './auth';
import ChangePasswordPage from './ChangePasswordPage';

function AppRouter() {
  return (
    <BrowserRouter>
      <StrictMode>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route element={<AuthProvider />}>
            <Route
              path="/change-password"
              element={
                <RequireForcedPasswordChange>
                  <ChangePasswordPage />
                </RequireForcedPasswordChange>
              }
            />

            <Route element={<RequireAuth />}>
              <Route element={<AppLayout />}>
                <Route index element={<DashboardPage />} />
                <Route path="sales" element={<SalesPage />} />
                <Route
                  path="sales-manager"
                  element={
                    <PlaceholderPage
                      title="Sales Manager"
                      description="Manage open sales, line voids, and sale corrections."
                    />
                  }
                />
                <Route path="customers" element={<CustomersPage />} />
                <Route
                  path="suppliers"
                  element={
                    <PlaceholderPage
                      title="Suppliers"
                      description="Manage supplier contacts and their linked products."
                    />
                  }
                />
                <Route path="inventory" element={<InventoryPage />} />
                <Route path="close" element={<ClosePage />} />

                <Route element={<RequireRole roles={['manager', 'admin']} />}>
                  <Route path="inventory/new" element={<ProductPage />} />
                  <Route path="inventory/product/:id" element={<ProductPage />} />
                </Route>

                <Route element={<RequireRole roles={['manager', 'admin']} />}>
                  <Route path="users" element={<UsersPage />} />
                  <Route path="settings" element={<SettingsPage />} />
                </Route>

                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Route>
          </Route>
        </Routes>
      </StrictMode>
    </BrowserRouter>
  );
}

export default AppRouter;
