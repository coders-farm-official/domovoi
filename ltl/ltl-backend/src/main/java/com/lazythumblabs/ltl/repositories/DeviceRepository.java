package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.Device;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface DeviceRepository extends JpaRepository<Device, Long> {

    Optional<Device> findByDeviceUid(String deviceUid);

    List<Device> findByHouseholdIdOrderByRegisteredAtDesc(Long householdId);

    long countByHouseholdIdAndRevokedAtIsNull(Long householdId);
}
