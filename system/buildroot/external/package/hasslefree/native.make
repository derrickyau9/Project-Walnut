# Invoked only through Buildroot with its target compiler/environment.
HF_SOURCE ?= .
HF_OBJECT_DIR ?= .hf-buildroot-objects
HF_INCLUDES = -I$(HF_SOURCE)/include -I$(HF_SOURCE)/third_party -I$(HF_SOURCE)/third_party/sqlite
HF_CPP_UNITS = core dng sha256 calibration daemon camerad worker
HF_CPP_OBJECTS = $(addprefix $(HF_OBJECT_DIR)/,$(addsuffix .o,$(HF_CPP_UNITS)))

.PHONY: all
all: hf-camerad hf-native-worker

$(HF_OBJECT_DIR):
	mkdir -p $@

$(HF_OBJECT_DIR)/%.o: $(HF_SOURCE)/src/%.cpp | $(HF_OBJECT_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -ffp-contract=off -pthread $(HF_INCLUDES) -MMD -MP -c $< -o $@

$(HF_OBJECT_DIR)/sqlite3.o: $(HF_SOURCE)/third_party/sqlite/sqlite3.c | $(HF_OBJECT_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DSQLITE_THREADSAFE=1 -DSQLITE_DQS=0 -pthread -MMD -MP -c $< -o $@

hf-camerad: $(addprefix $(HF_OBJECT_DIR)/,camerad.o daemon.o dng.o core.o sqlite3.o sha256.o calibration.o)
	$(CXX) $(LDFLAGS) $^ -pthread -ldl -o $@

hf-native-worker: $(addprefix $(HF_OBJECT_DIR)/,worker.o dng.o core.o)
	$(CXX) $(LDFLAGS) $^ -pthread -ldl -o $@

-include $(HF_CPP_OBJECTS:.o=.d) $(HF_OBJECT_DIR)/sqlite3.d
