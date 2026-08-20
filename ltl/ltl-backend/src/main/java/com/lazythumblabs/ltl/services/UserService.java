package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.repositories.UserRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Service
public class UserService {

    @Autowired
    private UserRepository userRepository;

    public Optional<User> findByStytchUserId(String stytchUserId) {
        return userRepository.findByStytchUserId(stytchUserId);
    }

    /**
     * Find or create the local account for an authenticated Stytch user.
     *
     * <p>Provisioning on first sight rather than in a signup webhook
     * keeps the two systems from being able to disagree about whether an
     * account exists.
     */
    @Transactional
    public User ensureUser(String stytchUserId, String email, String displayName) {
        return userRepository.findByStytchUserId(stytchUserId).orElseGet(() -> {
            User existing = email == null ? null
                    : userRepository.findByEmail(email).orElse(null);
            User user = existing == null ? new User() : existing;
            user.setStytchUserId(stytchUserId);
            if (email != null) {
                user.setEmail(email);
            }
            if (displayName != null) {
                user.setDisplayName(displayName);
            }
            return userRepository.save(user);
        });
    }

    public User save(User user) {
        return userRepository.save(user);
    }
}
