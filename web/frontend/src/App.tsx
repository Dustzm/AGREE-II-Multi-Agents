import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { AuthProvider } from './auth/AuthContext'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Chat } from './pages/Chat'
import { Reports } from './pages/Reports'
import { Docs } from './pages/Docs'
import { Login } from './pages/Login'

import { Skills } from './pages/Skills'
import { Agents } from './pages/Agents'
import { Batch } from './pages/Batch'
import { Benchmark } from './pages/Benchmark'
import { Config } from './pages/Config'
import { Users } from './pages/Users'

function App() {
  return (
    <BrowserRouter basename="/ama">
      <AuthProvider>
        <Toaster position="top-center" />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/docs" element={<Docs />} />
            <Route path="/" element={<Layout />}>
              <Route index element={<Navigate to="/chat" replace />} />
              <Route path="chat" element={<Chat />} />
              <Route path="reports" element={<Reports />} />
              <Route path="skills" element={<Skills />} />
              <Route path="agents" element={<Agents />} />
              <Route path="batch" element={<Batch />} />
              <Route path="benchmark" element={<Benchmark />} />
              <Route path="config" element={<Config />} />
              <Route path="users" element={<Users />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
