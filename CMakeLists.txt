cmake_minimum_required(VERSION 3.20)

project(equilane_native VERSION 0.1.0 LANGUAGES C)

include(CheckCCompilerFlag)
include(CTest)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)

option(IOITF_BUILD_NATIVE_ADAPTER
       "Build the architecture-specific Intel, OpenPOWER, ARM or RISC-V adapter" ON)
option(IOITF_BUILD_PORTABLE_ADAPTER
       "Build the development-only portable adapter" ON)

set(IOITF_STRICT_FP_OPTIONS)
set(IOITF_STRICT_FP_VERIFIED FALSE)
if(CMAKE_C_COMPILER_ID MATCHES "Clang")
  check_c_compiler_flag("-ffp-model=strict" IOITF_HAS_FFP_MODEL_STRICT)
  if(IOITF_HAS_FFP_MODEL_STRICT)
    list(APPEND IOITF_STRICT_FP_OPTIONS -ffp-model=strict)
    set(IOITF_STRICT_FP_VERIFIED TRUE)
  endif()
elseif(CMAKE_C_COMPILER_ID STREQUAL "GNU")
  foreach(flag IN ITEMS
          -fno-fast-math
          -ffp-contract=off
          -frounding-math
          -ftrapping-math)
    string(MAKE_C_IDENTIFIER "IOITF_HAS_${flag}" flag_variable)
    check_c_compiler_flag("${flag}" "${flag_variable}")
    if(${flag_variable})
      list(APPEND IOITF_STRICT_FP_OPTIONS "${flag}")
    endif()
  endforeach()
  list(LENGTH IOITF_STRICT_FP_OPTIONS IOITF_GNU_STRICT_FP_OPTION_COUNT)
  if(IOITF_GNU_STRICT_FP_OPTION_COUNT EQUAL 4)
    set(IOITF_STRICT_FP_VERIFIED TRUE)
  endif()
elseif(CMAKE_C_COMPILER_ID STREQUAL "IntelLLVM")
  check_c_compiler_flag("-fp-model=strict" IOITF_HAS_INTEL_FP_MODEL_STRICT)
  check_c_compiler_flag("-no-ftz" IOITF_HAS_INTEL_NO_FTZ)
  if(IOITF_HAS_INTEL_FP_MODEL_STRICT AND IOITF_HAS_INTEL_NO_FTZ)
    list(APPEND IOITF_STRICT_FP_OPTIONS -fp-model=strict -no-ftz)
    set(IOITF_STRICT_FP_VERIFIED TRUE)
  endif()
endif()

function(ioitf_configure_adapter target)
  target_include_directories(
    "${target}"
    PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include"
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/adapters/common")
  target_compile_options("${target}" PRIVATE ${IOITF_STRICT_FP_OPTIONS})
  if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang|IntelLLVM")
    target_compile_options("${target}" PRIVATE -Wall -Wextra -Wpedantic)
  endif()
  if(UNIX AND NOT APPLE)
    target_link_libraries("${target}" PRIVATE m)
  endif()
endfunction()

set(IOITF_COMMON_ADAPTER_SOURCE adapters/common/f64x2_adapter.c)

if(IOITF_BUILD_PORTABLE_ADAPTER)
  add_library(ioitf_adapter_portable SHARED
              ${IOITF_COMMON_ADAPTER_SOURCE}
              adapters/portable/sse2_pd.c)
  ioitf_configure_adapter(ioitf_adapter_portable)
  target_compile_definitions(ioitf_adapter_portable
                             PRIVATE IOITF_DEVELOPMENT_PORTABLE_ADAPTER=1)
endif()

string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" IOITF_SYSTEM_PROCESSOR)
set(IOITF_NATIVE_TARGET "")

if(IOITF_BUILD_NATIVE_ADAPTER AND CMAKE_SYSTEM_NAME STREQUAL "Linux" AND
   IOITF_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64)$")
  if(NOT IOITF_STRICT_FP_VERIFIED)
    message(FATAL_ERROR
      "The evidence-producing Intel adapter requires verified strict FP flags")
  endif()
  add_library(ioitf_adapter_intel SHARED
              ${IOITF_COMMON_ADAPTER_SOURCE}
              adapters/intel/sse2_pd.c)
  ioitf_configure_adapter(ioitf_adapter_intel)
  add_library(ioitf_intel_official_suite OBJECT
              10_official_suite/intel/f32x4.c
              10_official_suite/intel/f64x2.c
              10_official_suite/intel/i8x16.c
              10_official_suite/intel/i16x8.c
              10_official_suite/intel/i32x4.c
              10_official_suite/intel/i64x2.c)
  if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang|IntelLLVM")
    # Keep the complete case build unit within its declared SSE2 requirement.
    target_compile_options(ioitf_adapter_intel PRIVATE
      -march=x86-64
      -msse2
      -mno-sse3
      -mno-ssse3
      -mno-sse4.1
      -mno-sse4.2
      -mno-avx
      -mno-avx2
      -mno-avx512f)
    target_compile_options(ioitf_intel_official_suite PRIVATE
      -march=x86-64
      -msse2
      -mno-sse3
      -mno-ssse3
      -mno-sse4.1
      -mno-sse4.2
      -mno-avx
      -mno-avx2
      -mno-avx512f
      -Wall
      -Wextra
      -Wpedantic)
  endif()
  set(IOITF_NATIVE_TARGET ioitf_adapter_intel)
  set(IOITF_NATIVE_TEST_DEFINITION IOITF_TEST_INTEL_ADAPTER=1)
elseif(IOITF_BUILD_NATIVE_ADAPTER AND CMAKE_SYSTEM_NAME STREQUAL "Linux" AND
       IOITF_SYSTEM_PROCESSOR MATCHES "^(ppc64le|powerpc64le)$")
  if(NOT IOITF_STRICT_FP_VERIFIED)
    message(FATAL_ERROR
      "The evidence-producing OpenPOWER adapter requires verified strict FP flags")
  endif()
  add_library(ioitf_adapter_openpower SHARED
              ${IOITF_COMMON_ADAPTER_SOURCE}
              adapters/openpower/sse2_pd.c)
  ioitf_configure_adapter(ioitf_adapter_openpower)
  target_include_directories(ioitf_adapter_openpower
                             PRIVATE adapters/openpower)
  add_library(ioitf_openpower_official_suite OBJECT
              10_official_suite/openpower/f32x4.c
              10_official_suite/openpower/f64x2.c
              10_official_suite/openpower/i8x16.c
              10_official_suite/openpower/i16x8.c
              10_official_suite/openpower/i32x4.c
              10_official_suite/openpower/i64x2.c)
  if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    # power8 is the minimum implementation profile for these VSX wrappers.
    target_compile_options(ioitf_adapter_openpower PRIVATE
                           -mcpu=power8 -maltivec -mvsx)
    target_compile_options(ioitf_openpower_official_suite PRIVATE
                           -mcpu=power8 -maltivec -mvsx
                           -Wall -Wextra -Wpedantic)
  endif()
  set(IOITF_NATIVE_TARGET ioitf_adapter_openpower)
  set(IOITF_NATIVE_TEST_DEFINITION IOITF_TEST_OPENPOWER_ADAPTER=1)
elseif(IOITF_BUILD_NATIVE_ADAPTER AND
       IOITF_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64|riscv64)$")
  if(NOT IOITF_STRICT_FP_VERIFIED)
    message(FATAL_ERROR "The native adapter requires verified strict FP flags")
  endif()
  if(IOITF_SYSTEM_PROCESSOR STREQUAL "riscv64")
    set(IOITF_NEW_ROLE riscv)
    set(IOITF_NEW_OPTIONS -march=rv64gcv -mabi=lp64d -mrvv-vector-bits=scalable)
    set(IOITF_NATIVE_TEST_DEFINITION IOITF_TEST_RISCV_ADAPTER=1)
  else()
    set(IOITF_NEW_ROLE arm)
    set(IOITF_NEW_OPTIONS -march=armv8-a+simd)
    set(IOITF_NATIVE_TEST_DEFINITION IOITF_TEST_ARM_ADAPTER=1)
  endif()
  set(IOITF_NATIVE_TARGET ioitf_adapter_${IOITF_NEW_ROLE})
  add_library(${IOITF_NATIVE_TARGET} SHARED
              ${IOITF_COMMON_ADAPTER_SOURCE}
              adapters/${IOITF_NEW_ROLE}/sse2_pd.c)
  ioitf_configure_adapter(${IOITF_NATIVE_TARGET})
  target_include_directories(${IOITF_NATIVE_TARGET} PRIVATE adapters/${IOITF_NEW_ROLE})
  target_compile_options(${IOITF_NATIVE_TARGET} PRIVATE ${IOITF_NEW_OPTIONS})
  add_library(ioitf_${IOITF_NEW_ROLE}_official_suite OBJECT
              10_official_suite/${IOITF_NEW_ROLE}/f32x4.c
              10_official_suite/${IOITF_NEW_ROLE}/f64x2.c
              10_official_suite/${IOITF_NEW_ROLE}/i8x16.c
              10_official_suite/${IOITF_NEW_ROLE}/i16x8.c
              10_official_suite/${IOITF_NEW_ROLE}/i32x4.c
              10_official_suite/${IOITF_NEW_ROLE}/i64x2.c)
  target_compile_options(ioitf_${IOITF_NEW_ROLE}_official_suite PRIVATE
                         ${IOITF_NEW_OPTIONS} ${IOITF_STRICT_FP_OPTIONS}
                         -Wall -Wextra -Wpedantic)
  if(BUILD_TESTING AND IOITF_NEW_ROLE STREQUAL "arm")
    add_executable(ioitf_arm_wrappers_selftest tests/native/test_arm_wrappers.c
                   $<TARGET_OBJECTS:ioitf_arm_official_suite>)
    target_compile_options(ioitf_arm_wrappers_selftest PRIVATE
                           ${IOITF_STRICT_FP_OPTIONS} ${IOITF_NEW_OPTIONS})
    target_include_directories(ioitf_arm_wrappers_selftest PRIVATE
                               10_official_suite/arm)
    if(UNIX AND NOT APPLE)
      target_link_libraries(ioitf_arm_wrappers_selftest PRIVATE m)
    endif()
    add_test(NAME arm_neon_wrappers COMMAND ioitf_arm_wrappers_selftest)
  endif()
elseif(IOITF_BUILD_NATIVE_ADAPTER)
  message(STATUS
    "No evidence-producing native adapter for ${CMAKE_SYSTEM_PROCESSOR}; "
    "only the development portable adapter can be built")
endif()

if(BUILD_TESTING AND IOITF_BUILD_PORTABLE_ADAPTER)
  add_executable(ioitf_native_selftest
                 tests/native/test_native.c
                 ${IOITF_COMMON_ADAPTER_SOURCE})
  target_include_directories(ioitf_native_selftest
                             PRIVATE
                             "${CMAKE_CURRENT_SOURCE_DIR}/include"
                             "${CMAKE_CURRENT_SOURCE_DIR}/adapters/common")
  target_link_libraries(ioitf_native_selftest PRIVATE ioitf_adapter_portable)
  if(UNIX AND NOT APPLE)
    target_link_libraries(ioitf_native_selftest PRIVATE m)
  endif()
  target_compile_options(ioitf_native_selftest PRIVATE ${IOITF_STRICT_FP_OPTIONS})
  if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(ioitf_native_selftest
                           PRIVATE -Wall -Wextra -Wpedantic)
  endif()
  if(IOITF_NATIVE_TARGET)
    target_link_libraries(ioitf_native_selftest PRIVATE ${IOITF_NATIVE_TARGET})
    target_compile_definitions(ioitf_native_selftest
                               PRIVATE ${IOITF_NATIVE_TEST_DEFINITION})
  endif()
  add_test(NAME native_abi_and_sse2_cases COMMAND ioitf_native_selftest)
endif()

install(FILES
        include/framework/case_abi.h
        include/framework/example_cases.h
        DESTINATION include/framework)
if(TARGET ioitf_adapter_intel)
  install(TARGETS ioitf_adapter_intel LIBRARY DESTINATION lib)
endif()
if(TARGET ioitf_adapter_openpower)
  install(TARGETS ioitf_adapter_openpower LIBRARY DESTINATION lib)
endif()
foreach(role IN ITEMS arm riscv)
  if(TARGET ioitf_adapter_${role})
    install(TARGETS ioitf_adapter_${role} LIBRARY DESTINATION lib)
  endif()
endforeach()
