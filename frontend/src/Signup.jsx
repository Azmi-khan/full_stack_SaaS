import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import axios from 'axios'
import './Auth.css'

function Signup() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const navigate = useNavigate()

  const handleSignup = async (e) => {
    e.preventDefault()
    setError('')

    try {
      // main.py's /signup expects a JSON body matching schemas.UserCreate
      await axios.post('http://127.0.0.1:8000/signup', { email, password })

      // Send them to login with a flag so it can show a success message
      navigate('/login', { state: { justSignedUp: true } })
    } catch (err) {
      if (err.response?.status === 400) {
        setError('An account with that email already exists.')
      } else {
        setError('Could not create account. Please try again.')
      }
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="auth-brand-mark">◈</span>
          PERCEPTION<span className="auth-brand-thin">.SYS</span>
        </div>
        <p className="auth-subtitle">Create an account to access the perception console</p>

        {error && <p className="auth-error">{error}</p>}

        <form onSubmit={handleSignup} className="auth-form">
          <div className="auth-field">
            <label htmlFor="signup-email">Email</label>
            <input
              id="signup-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>

          <div className="auth-field">
            <label htmlFor="signup-password">Password</label>
            <input
              id="signup-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
              minLength={8}
            />
          </div>

          <button type="submit" className="auth-submit">
            Create account
          </button>
        </form>

        <p className="auth-switch">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  )
}

export default Signup